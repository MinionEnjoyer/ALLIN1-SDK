"""Versioned ALLIN1 content-extension manifests and runtime registry.

The desktop launcher deliberately consumes only declarative extension metadata.
Managed code is loaded by the Story Mode runtime, never imported into Python.
Package receipts are the authority for whether third-party content may run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from allin1_sdk.mod_package_contract import (
    WeaponEnhancementContract,
    parse_workbench_contract,
)


log = logging.getLogger("allin1.extensions")


EXTENSION_SCHEMA_VERSION = 1
EXTENSION_API_VERSION = 1
REGISTRY_SCHEMA_VERSION = 1
PRELOAD_MANIFEST_SCHEMA_VERSION = 1
PRELOAD_MANIFEST_ID = "allin1"
PRELOAD_MAX_ENTRIES = 64
PRELOAD_MAX_ENTRY_BYTES = 4 * 1024 * 1024
PRELOAD_MAX_AGGREGATE_BYTES = 16 * 1024 * 1024
PRELOAD_MAX_MANIFEST_BYTES = 256 * 1024
PRELOAD_STATIC_ENTRIES: tuple[dict[str, Any], ...] = (
    {
        "id": "config",
        "path": "scripts/ALLIN1.toml",
        "kind": "text",
        "required": True,
        "max_bytes": 256 * 1024,
    },
    {
        "id": "extension-registry",
        "path": "scripts/.allin1/extensions/registry.json",
        "kind": "json",
        "required": False,
        "max_bytes": PRELOAD_MAX_ENTRY_BYTES,
    },
    {
        "id": "characters",
        "path": "scripts/ALLIN1_characters.json",
        "kind": "json",
        "required": False,
        "max_bytes": PRELOAD_MAX_ENTRY_BYTES,
    },
    {
        "id": "gear-prices",
        "path": "scripts/prices_gear.toml",
        "kind": "text",
        "required": False,
        "max_bytes": 256 * 1024,
    },
    {
        "id": "story-vehicle-catalog",
        "path": "scripts/ALLIN1/Catalogs/story-vehicles.json",
        "kind": "json",
        "required": False,
        "max_bytes": PRELOAD_MAX_ENTRY_BYTES,
    },
    {
        "id": "garage-map-runtime",
        "path": "scripts/ALLIN1/Maps/runtime-detected.json",
        "kind": "json",
        "required": False,
        "max_bytes": 1024 * 1024,
    },
)
SUPPORTED_SETTING_TYPES = frozenset({
    "boolean", "integer", "number", "string", "choice",
})
SUPPORTED_CATALOG_KINDS = frozenset({
    "vehicle", "weapon", "gear", "service", "property",
})
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,95}$")
_SETTING_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_CONFIG_KEY_PATTERN = re.compile(
    r"^(general|traffic|vehicles|script)\.[a-z][a-z0-9_]*$"
)
_REQUIREMENT_PATTERN = re.compile(
    r"^([a-z0-9][a-z0-9._-]{1,63})(?:(==|>=)([0-9]+(?:\.[0-9]+){0,3}))?$"
)
_WINDOWS_INVALID_CHARS = frozenset('<>:"|?*')
_WINDOWS_DEVICE_NAMES = frozenset({
    "con", "prn", "aux", "nul", "conin$", "conout$",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
})
_MANIFEST_FIELDS = frozenset({
    "schema_version", "api_version", "id", "name", "version",
    "description", "capabilities", "systems", "gbay", "runtime", "workbench",
})


def _safe_relative(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty relative path")
    normalized = value.replace("\\", "/")
    if normalized != normalized.strip():
        raise ValueError(f"{label} must not begin or end with whitespace")
    components = normalized.split("/")
    if any(part in {"", ".", ".."} for part in components):
        raise ValueError(f"{label} must not be absolute or contain traversal segments")
    for component in components:
        if component.endswith((".", " ")):
            raise ValueError(
                f"{label} contains a Windows path component ending in a dot or space"
            )
        if any(
            character in _WINDOWS_INVALID_CHARS or ord(character) < 32
            for character in component
        ):
            raise ValueError(f"{label} contains characters invalid on Windows")
        device_stem = component.split(".", 1)[0].casefold()
        if device_stem in _WINDOWS_DEVICE_NAMES:
            raise ValueError(f"{label} contains a reserved Windows device name")
    path = PurePosixPath(normalized)
    if path.is_absolute():
        raise ValueError(f"{label} must be relative")
    return path


@dataclass(frozen=True)
class _ContentRequirement:
    extension_id: str
    operator: str | None
    version: str | None

    @classmethod
    def parse(cls, value: object) -> "_ContentRequirement":
        if not isinstance(value, str):
            raise ValueError("content requirements must be strings")
        normalized = value.strip().lower().replace(" ", "")
        match = _REQUIREMENT_PATTERN.fullmatch(normalized)
        if not match:
            raise ValueError(f"Invalid ALLIN1 content requirement: {value!r}")
        return cls(match.group(1), match.group(2), match.group(3))

    def normalized(self) -> str:
        if self.operator and self.version:
            return f"{self.extension_id}{self.operator}{self.version}"
        return self.extension_id

    def accepts(self, installed_version: str) -> bool:
        if self.operator is None or self.version is None:
            return True
        try:
            installed = tuple(int(part) for part in installed_version.split("."))
            required = tuple(int(part) for part in self.version.split("."))
        except ValueError:
            return False
        width = max(len(installed), len(required))
        installed += (0,) * (width - len(installed))
        required += (0,) * (width - len(required))
        if self.operator == "==":
            return installed == required
        return installed >= required


def _content_requirements(value: object) -> tuple[_ContentRequirement, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("content receipt requires must be an array")
    requirements = tuple(_ContentRequirement.parse(item) for item in value)
    ids = [requirement.extension_id for requirement in requirements]
    if len(ids) != len(set(ids)):
        raise ValueError("content receipt contains duplicate requirements")
    return requirements


def _required_text(data: Mapping[str, Any], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}.{key} must be a non-empty string")
    return value.strip()


def _reject_unknown(
    data: Mapping[str, Any], allowed: Iterable[str], label: str,
) -> None:
    unknown = set(data) - set(allowed)
    if unknown:
        raise ValueError(
            f"Unsupported {label} field(s): " + ", ".join(sorted(unknown))
        )


def _identifier(value: object, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not _ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            f"{label} must be 2-96 lowercase letters, numbers, dots, dashes, or underscores"
        )
    return normalized


def _string_tuple(value: object, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be an array of strings")
    return tuple(dict.fromkeys(item.strip() for item in value if item.strip()))


def _json_value(value: Any) -> Any:
    """Return a detached JSON-safe value or reject opaque Python objects."""
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError("extension defaults and settings must be JSON values") from exc


@dataclass(frozen=True)
class ExtensionSetting:
    key: str
    label: str
    setting_type: str
    default: Any
    description: str = ""
    group: str = "General"
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    config_key: str | None = None

    @classmethod
    def from_dict(cls, data: object, label: str) -> "ExtensionSetting":
        if not isinstance(data, dict):
            raise ValueError(f"{label} must be an object")
        _reject_unknown(data, {
            "key", "label", "type", "default", "description", "group",
            "choices", "minimum", "maximum", "step", "config_key",
        }, label)
        key = str(data.get("key", "")).strip().lower()
        if not _SETTING_PATTERN.fullmatch(key):
            raise ValueError(f"{label}.key must be a safe lowercase setting name")
        setting_type = str(data.get("type", "")).strip().lower()
        if setting_type not in SUPPORTED_SETTING_TYPES:
            raise ValueError(
                f"{label}.type must be one of {', '.join(sorted(SUPPORTED_SETTING_TYPES))}"
            )
        choices = _string_tuple(data.get("choices"), f"{label}.choices")
        if setting_type == "choice" and not choices:
            raise ValueError(f"{label}.choices is required for a choice setting")
        if setting_type != "choice" and choices:
            raise ValueError(f"{label}.choices is valid only for a choice setting")
        minimum = data.get("minimum")
        maximum = data.get("maximum")
        step = data.get("step")
        for field_name, field_value in (
            ("minimum", minimum), ("maximum", maximum), ("step", step),
        ):
            if field_value is not None and (
                isinstance(field_value, bool) or not isinstance(field_value, (int, float))
                or not math.isfinite(float(field_value))
            ):
                raise ValueError(f"{label}.{field_name} must be a finite number")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ValueError(f"{label}.minimum must not exceed maximum")
        if step is not None and step <= 0:
            raise ValueError(f"{label}.step must be positive")
        if setting_type not in {"integer", "number"} and any(
            value is not None for value in (minimum, maximum, step)
        ):
            raise ValueError(
                f"{label} numeric bounds are valid only for integer or number settings"
            )
        config_key_value = data.get("config_key")
        config_key = None
        if config_key_value is not None:
            config_key = str(config_key_value).strip().lower()
            if not _CONFIG_KEY_PATTERN.fullmatch(config_key):
                raise ValueError(f"{label}.config_key is not a supported core setting path")
        setting = cls(
            key=key,
            label=_required_text(data, "label", label),
            setting_type=setting_type,
            default=_json_value(data.get("default")),
            description=str(data.get("description", "")).strip(),
            group=str(data.get("group", "General")).strip() or "General",
            choices=choices,
            minimum=float(minimum) if minimum is not None else None,
            maximum=float(maximum) if maximum is not None else None,
            step=float(step) if step is not None else None,
            config_key=config_key,
        )
        setting.validate(setting.default)
        return setting

    def validate(self, value: Any) -> Any:
        if self.setting_type == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{self.key} must be true or false")
        elif self.setting_type == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{self.key} must be an integer")
        elif self.setting_type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{self.key} must be a number")
            if not math.isfinite(float(value)):
                raise ValueError(f"{self.key} must be finite")
        elif self.setting_type in {"string", "choice"}:
            if not isinstance(value, str):
                raise ValueError(f"{self.key} must be text")
        if self.choices and value not in self.choices:
            raise ValueError(
                f"{self.key} must be one of {', '.join(self.choices)}"
            )
        if self.minimum is not None and float(value) < self.minimum:
            raise ValueError(f"{self.key} must be at least {self.minimum:g}")
        if self.maximum is not None and float(value) > self.maximum:
            raise ValueError(f"{self.key} must be at most {self.maximum:g}")
        return _json_value(value)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "type": self.setting_type,
            "default": self.default,
            "description": self.description,
            "group": self.group,
        }
        if self.choices:
            result["choices"] = list(self.choices)
        if self.minimum is not None:
            result["minimum"] = self.minimum
        if self.maximum is not None:
            result["maximum"] = self.maximum
        if self.step is not None:
            result["step"] = self.step
        if self.config_key:
            result["config_key"] = self.config_key
        return result


@dataclass(frozen=True)
class ExtensionSystem:
    system_id: str
    name: str
    description: str
    category: str
    experimental: bool
    enabled_by_default: bool
    settings: tuple[ExtensionSetting, ...]

    @classmethod
    def from_dict(cls, data: object, index: int) -> "ExtensionSystem":
        label = f"systems[{index}]"
        if not isinstance(data, dict):
            raise ValueError(f"{label} must be an object")
        _reject_unknown(data, {
            "id", "name", "description", "category", "experimental",
            "enabled_by_default", "settings",
        }, label)
        raw_settings = data.get("settings", [])
        if not isinstance(raw_settings, list):
            raise ValueError(f"{label}.settings must be an array")
        settings = tuple(
            ExtensionSetting.from_dict(item, f"{label}.settings[{setting_index}]")
            for setting_index, item in enumerate(raw_settings, start=1)
        )
        setting_keys = [setting.key for setting in settings]
        if len(setting_keys) != len(set(setting_keys)):
            raise ValueError(f"{label} contains duplicate setting keys")
        experimental = data.get("experimental", False)
        enabled = data.get("enabled_by_default", True)
        if not isinstance(experimental, bool) or not isinstance(enabled, bool):
            raise ValueError(f"{label} boolean flags must be true or false")
        return cls(
            system_id=_identifier(data.get("id"), f"{label}.id"),
            name=_required_text(data, "name", label),
            description=str(data.get("description", "")).strip(),
            category=str(data.get("category", "Other")).strip() or "Other",
            experimental=experimental,
            enabled_by_default=enabled,
            settings=settings,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.system_id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "experimental": self.experimental,
            "enabled_by_default": self.enabled_by_default,
            "settings": [setting.to_dict() for setting in self.settings],
        }


@dataclass(frozen=True)
class GbaySection:
    section_id: str
    label: str
    description: str
    route: str
    order: int

    @classmethod
    def from_dict(cls, data: object, index: int) -> "GbaySection":
        label = f"gbay.sections[{index}]"
        if not isinstance(data, dict):
            raise ValueError(f"{label} must be an object")
        _reject_unknown(data, {"id", "label", "description", "route", "order"}, label)
        order = data.get("order", 100)
        if isinstance(order, bool) or not isinstance(order, int):
            raise ValueError(f"{label}.order must be an integer")
        route = _required_text(data, "route", label)
        if not re.fullmatch(r"[a-z][a-z0-9._:-]{0,95}", route):
            raise ValueError(f"{label}.route is invalid")
        return cls(
            section_id=_identifier(data.get("id"), f"{label}.id"),
            label=_required_text(data, "label", label),
            description=str(data.get("description", "")).strip(),
            route=route,
            order=order,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.section_id,
            "label": self.label,
            "description": self.description,
            "route": self.route,
            "order": self.order,
        }


@dataclass(frozen=True)
class GbayCatalog:
    catalog_id: str
    kind: str
    source: PurePosixPath

    @classmethod
    def from_dict(cls, data: object, index: int) -> "GbayCatalog":
        label = f"gbay.catalogs[{index}]"
        if not isinstance(data, dict):
            raise ValueError(f"{label} must be an object")
        _reject_unknown(data, {"id", "kind", "source"}, label)
        kind = str(data.get("kind", "")).strip().lower()
        if kind not in SUPPORTED_CATALOG_KINDS:
            raise ValueError(
                f"{label}.kind must be one of {', '.join(sorted(SUPPORTED_CATALOG_KINDS))}"
            )
        source = _safe_relative(data.get("source"), f"{label}.source")
        if source.suffix.casefold() != ".json":
            raise ValueError(f"{label}.source must be a JSON file")
        return cls(
            catalog_id=_identifier(data.get("id"), f"{label}.id"),
            kind=kind,
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.catalog_id,
            "kind": self.kind,
            "source": self.source.as_posix(),
        }


@dataclass(frozen=True)
class RuntimeAssembly:
    path: PurePosixPath
    entry_point: str | None

    @classmethod
    def from_dict(cls, data: object, index: int) -> "RuntimeAssembly":
        label = f"runtime.assemblies[{index}]"
        if not isinstance(data, dict):
            raise ValueError(f"{label} must be an object")
        _reject_unknown(data, {"path", "entry_point"}, label)
        path = _safe_relative(data.get("path"), f"{label}.path")
        lowered = tuple(part.casefold() for part in path.parts)
        if not lowered or lowered[0] != "scripts" or path.suffix.casefold() != ".dll":
            raise ValueError(f"{label}.path must be a DLL below scripts/")
        entry_value = data.get("entry_point")
        entry_point = None if entry_value is None else str(entry_value).strip()
        if entry_value is not None and not entry_point:
            raise ValueError(f"{label}.entry_point must not be empty")
        return cls(path=path, entry_point=entry_point)

    def to_dict(self) -> dict[str, Any]:
        result = {"path": self.path.as_posix()}
        if self.entry_point:
            result["entry_point"] = self.entry_point
        return result


@dataclass(frozen=True)
class ExtensionManifest:
    manifest_path: Path
    extension_id: str
    name: str
    version: str
    description: str
    api_version: int
    capabilities: tuple[str, ...]
    systems: tuple[ExtensionSystem, ...]
    gbay_sections: tuple[GbaySection, ...]
    gbay_catalogs: tuple[GbayCatalog, ...]
    runtime_assemblies: tuple[RuntimeAssembly, ...]
    workbench_weapon_enhancements: tuple[WeaponEnhancementContract, ...] = ()

    @classmethod
    def load(cls, manifest_path: str | Path) -> "ExtensionManifest":
        path = Path(manifest_path).resolve()
        if path.is_dir():
            path = path / "allin1.content.json"
        if not path.is_file():
            raise FileNotFoundError(f"ALLIN1 content manifest not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid ALLIN1 content manifest: {exc}") from exc
        return cls.from_dict(data, path)

    @classmethod
    def from_dict(
        cls, data: object, manifest_path: str | Path = "allin1.content.json",
    ) -> "ExtensionManifest":
        if not isinstance(data, dict):
            raise ValueError("ALLIN1 content manifest must be a JSON object")
        _reject_unknown(data, _MANIFEST_FIELDS, "content manifest")
        if data.get("schema_version") != EXTENSION_SCHEMA_VERSION:
            raise ValueError(
                f"content schema_version must be {EXTENSION_SCHEMA_VERSION}"
            )
        api_version = data.get("api_version")
        if api_version != EXTENSION_API_VERSION:
            raise ValueError(
                f"content api_version {api_version!r} is not supported; "
                f"this launcher supports API {EXTENSION_API_VERSION}"
            )
        path = Path(manifest_path)
        raw_systems = data.get("systems", [])
        if not isinstance(raw_systems, list):
            raise ValueError("content systems must be an array")
        systems = tuple(
            ExtensionSystem.from_dict(item, index)
            for index, item in enumerate(raw_systems, start=1)
        )
        system_ids = [system.system_id for system in systems]
        if len(system_ids) != len(set(system_ids)):
            raise ValueError("content manifest contains duplicate system ids")
        setting_keys = [
            setting.key for system in systems for setting in system.settings
        ]
        if len(setting_keys) != len(set(setting_keys)):
            raise ValueError("setting keys must be unique across a content package")

        gbay = data.get("gbay", {})
        if not isinstance(gbay, dict):
            raise ValueError("content gbay must be an object")
        _reject_unknown(gbay, {"sections", "catalogs"}, "content gbay")
        raw_sections = gbay.get("sections", [])
        raw_catalogs = gbay.get("catalogs", [])
        if not isinstance(raw_sections, list) or not isinstance(raw_catalogs, list):
            raise ValueError("gbay sections and catalogs must be arrays")
        sections = tuple(
            GbaySection.from_dict(item, index)
            for index, item in enumerate(raw_sections, start=1)
        )
        catalogs = tuple(
            GbayCatalog.from_dict(item, index)
            for index, item in enumerate(raw_catalogs, start=1)
        )
        section_ids = [section.section_id for section in sections]
        catalog_ids = [catalog.catalog_id for catalog in catalogs]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("content manifest contains duplicate GBAY section ids")
        if len(catalog_ids) != len(set(catalog_ids)):
            raise ValueError("content manifest contains duplicate GBAY catalog ids")

        runtime = data.get("runtime", {})
        if not isinstance(runtime, dict):
            raise ValueError("content runtime must be an object")
        _reject_unknown(runtime, {"assemblies"}, "content runtime")
        raw_assemblies = runtime.get("assemblies", [])
        if not isinstance(raw_assemblies, list):
            raise ValueError("runtime assemblies must be an array")
        assemblies = tuple(
            RuntimeAssembly.from_dict(item, index)
            for index, item in enumerate(raw_assemblies, start=1)
        )
        paths = [assembly.path.as_posix().casefold() for assembly in assemblies]
        if len(paths) != len(set(paths)):
            raise ValueError("content manifest contains duplicate runtime assemblies")
        workbench_weapon_enhancements = parse_workbench_contract(
            data.get("workbench"),
            runtime_entry_points=(
                assembly.entry_point for assembly in assemblies
                if assembly.entry_point
            ),
        )
        capabilities = tuple(
            value.lower() for value in _string_tuple(
                data.get("capabilities"), "capabilities",
            )
        )
        for capability in capabilities:
            if not re.fullmatch(r"[a-z][a-z0-9._-]{1,95}", capability):
                raise ValueError(f"Invalid extension capability: {capability}")
        capability_set = set(capabilities)
        if sections and "gbay.sections" not in capability_set:
            raise ValueError("GBAY sections require the gbay.sections capability")
        if catalogs and "gbay.catalogs" not in capability_set:
            raise ValueError("GBAY catalogs require the gbay.catalogs capability")
        if any(system.settings for system in systems) and (
            "launcher.settings" not in capability_set
        ):
            raise ValueError(
                "Typed system settings require the launcher.settings capability"
            )
        if not systems and not sections and not catalogs and not assemblies:
            raise ValueError("content manifest must contribute at least one system or runtime item")
        return cls(
            manifest_path=path,
            extension_id=_identifier(data.get("id"), "content id"),
            name=_required_text(data, "name", "content"),
            version=_required_text(data, "version", "content"),
            description=str(data.get("description", "")).strip(),
            api_version=int(api_version),
            capabilities=capabilities,
            systems=systems,
            gbay_sections=sections,
            gbay_catalogs=catalogs,
            runtime_assemblies=assemblies,
            workbench_weapon_enhancements=workbench_weapon_enhancements,
        )

    @classmethod
    def from_registry_entry(
        cls, data: object, manifest_path: str | Path = "registry.json",
    ) -> "ExtensionManifest":
        """Rehydrate the descriptor portion of a normalized registry entry."""
        if not isinstance(data, dict):
            raise ValueError("ALLIN1 registry entry must be an object")
        return cls.from_dict(
            {key: data[key] for key in _MANIFEST_FIELDS if key in data},
            manifest_path,
        )

    @property
    def settings(self) -> tuple[ExtensionSetting, ...]:
        return tuple(setting for system in self.systems for setting in system.settings)

    def setting(self, key: str) -> ExtensionSetting:
        normalized = key.strip().lower()
        for setting in self.settings:
            if setting.key == normalized:
                return setting
        raise KeyError(f"Unknown setting '{key}' for {self.extension_id}")

    def validate_package_destinations(self, destinations: Iterable[str]) -> None:
        owned = {value.replace("\\", "/").casefold() for value in destinations}
        for assembly in self.runtime_assemblies:
            path = assembly.path.as_posix().casefold()
            # Built-in descriptors are registered directly by the core installer
            # and do not pass through this package-ownership check. Every package
            # manifest, including one that copies an official id, must own each
            # executable file it asks the runtime to authorize.
            if path not in owned:
                raise ValueError(
                    f"Runtime assembly is not owned by this package: {assembly.path}"
                )
        for catalog in self.gbay_catalogs:
            if catalog.source.as_posix().casefold() not in owned:
                raise ValueError(
                    f"GBAY catalog is not owned by this package: {catalog.source}"
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": EXTENSION_SCHEMA_VERSION,
            "api_version": self.api_version,
            "id": self.extension_id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "capabilities": list(self.capabilities),
            "systems": [system.to_dict() for system in self.systems],
            "gbay": {
                "sections": [section.to_dict() for section in self.gbay_sections],
                "catalogs": [catalog.to_dict() for catalog in self.gbay_catalogs],
            },
            "runtime": {
                "assemblies": [assembly.to_dict() for assembly in self.runtime_assemblies],
            },
            "workbench": {
                "weapon_enhancements": [
                    enhancement.to_dict()
                    for enhancement in self.workbench_weapon_enhancements
                ],
            },
        }


class ExtensionCatalog:
    """Discover content manifests without importing executable package code."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def discover(self) -> list[ExtensionManifest]:
        if not self.root.is_dir():
            return []
        paths = set(self.root.glob("*/allin1.content.json"))
        paths.update(self.root.glob("*.content.json"))
        manifests = [ExtensionManifest.load(path) for path in paths]
        manifests.sort(key=lambda manifest: manifest.extension_id)
        ids = [manifest.extension_id for manifest in manifests]
        if len(ids) != len(set(ids)):
            raise ValueError("content catalog contains duplicate package ids")
        return manifests


def settings_from_config(
    manifest: ExtensionManifest, config: object,
) -> dict[str, Any]:
    """Read compatibility-bound settings from the current core config object."""
    values: dict[str, Any] = {}
    for setting in manifest.settings:
        if not setting.config_key:
            continue
        section_name, field_name = setting.config_key.split(".", 1)
        section = getattr(config, section_name, None)
        if section is None or not hasattr(section, field_name):
            raise ValueError(
                f"Content setting {manifest.extension_id}:{setting.key} binds "
                f"to missing config field {setting.config_key}"
            )
        values[setting.key] = setting.validate(getattr(section, field_name))
    return values


def apply_settings_to_config(
    manifest: ExtensionManifest, config: object, values: Mapping[str, Any],
) -> None:
    """Apply only explicitly declared compatibility bindings to core config."""
    for key, value in values.items():
        setting = manifest.setting(key)
        if not setting.config_key:
            continue
        validated = setting.validate(value)
        section_name, field_name = setting.config_key.split(".", 1)
        section = getattr(config, section_name, None)
        if section is None or not hasattr(section, field_name):
            raise ValueError(f"Unknown core config binding: {setting.config_key}")
        setattr(section, field_name, validated)


class ExtensionSettingsStore:
    """Package-owned, namespaced settings stored outside the core TOML schema."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid extension settings file: {self.path}") from exc
        if payload.get("schema_version") != REGISTRY_SCHEMA_VERSION:
            raise ValueError("Unsupported extension settings schema")
        values = payload.get("extensions", {})
        if not isinstance(values, dict):
            raise ValueError("Extension settings must contain an extensions object")
        result: dict[str, dict[str, Any]] = {}
        for extension_id, settings in values.items():
            if not _ID_PATTERN.fullmatch(str(extension_id)) or not isinstance(settings, dict):
                raise ValueError("Extension settings contain an invalid package namespace")
            result[str(extension_id)] = dict(settings)
        return result

    def _write(self, values: Mapping[str, Mapping[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "extensions": values,
        }
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        backup = self.path.with_suffix(self.path.suffix + ".bak")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        if self.path.exists():
            shutil.copy2(self.path, backup)
        temporary.replace(self.path)

    def effective(self, manifest: ExtensionManifest) -> dict[str, Any]:
        stored = self._read().get(manifest.extension_id, {})
        effective: dict[str, Any] = {}
        for setting in manifest.settings:
            candidate = stored.get(setting.key, setting.default)
            try:
                effective[setting.key] = setting.validate(candidate)
            except ValueError:
                effective[setting.key] = setting.default
        return effective

    def update(
        self, manifest: ExtensionManifest, values: Mapping[str, Any],
    ) -> dict[str, Any]:
        current = self._read()
        namespace = dict(current.get(manifest.extension_id, {}))
        for key, value in values.items():
            setting = manifest.setting(key)
            namespace[setting.key] = setting.validate(value)
        current[manifest.extension_id] = namespace
        self._write(current)
        return self.effective(manifest)

    def remove(self, extension_id: str) -> None:
        current = self._read()
        if current.pop(extension_id, None) is not None:
            self._write(current)


@dataclass(frozen=True)
class _RegistryCandidate:
    manifest: ExtensionManifest
    requested_enabled: bool
    source: str
    runtime_files: tuple[dict[str, str], ...] = ()
    catalog_files: tuple[dict[str, str], ...] = ()
    map_files: tuple[dict[str, str], ...] = ()
    blocked_reason: str = ""
    requirements: tuple[_ContentRequirement, ...] = ()


class ExtensionRegistry:
    """Build the single receipt-authorized registry consumed by Story Mode."""

    def __init__(self, gta_path: str | Path) -> None:
        self.gta_path = Path(gta_path).expanduser().resolve()
        self.state_root = self.gta_path / "scripts" / ".allin1" / "extensions"
        self.builtin_root = self.state_root / "builtins"
        self.registry_path = self.state_root / "registry.json"
        self.settings = ExtensionSettingsStore(self.state_root / "settings.json")
        self.receipt_root = self.gta_path / "scripts" / ".allin1" / "mods"
        self.preload_manifest_path = (
            self.gta_path / "scripts" / ".reactorv" / "preload" / "allin1.json"
        )

    @staticmethod
    def _snapshot_file(path: Path) -> bytes | None:
        return path.read_bytes() if path.is_file() else None

    @staticmethod
    def _restore_file(path: Path, snapshot: bytes | None) -> None:
        if snapshot is None:
            path.unlink(missing_ok=True)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".rollback.tmp")
        temporary.write_bytes(snapshot)
        temporary.replace(path)

    def register_builtin(
        self,
        manifest: ExtensionManifest,
        *,
        enabled: bool | None = None,
        settings: Mapping[str, Any] | None = None,
        map_files: Iterable[Mapping[str, str]] | None = None,
    ) -> Path:
        self.builtin_root.mkdir(parents=True, exist_ok=True)
        target = self.builtin_root / f"{manifest.extension_id}.json"
        target_snapshot = self._snapshot_file(target)
        settings_snapshot = self._snapshot_file(self.settings.path)
        current_enabled = True
        if enabled is None and target.is_file():
            try:
                current_enabled = bool(
                    json.loads(target.read_text(encoding="utf-8")).get("enabled", True)
                )
            except (OSError, json.JSONDecodeError):
                current_enabled = True
        elif enabled is not None:
            current_enabled = bool(enabled)
        try:
            normalized_map_files = self._normalize_builtin_map_files(
                manifest, map_files,
            )
            payload = {
                "enabled": current_enabled,
                "extension": manifest.to_dict(),
                "map_files": normalized_map_files,
            }
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            temporary.replace(target)
            if settings:
                self.settings.update(manifest, settings)
            self.rebuild()
        except Exception:
            self._restore_file(target, target_snapshot)
            self._restore_file(self.settings.path, settings_snapshot)
            raise
        return target

    def set_builtin_enabled(self, extension_id: str, enabled: bool) -> None:
        normalized = _identifier(extension_id, "extension id")
        if not enabled:
            self._refuse_enabled_dependents(normalized)
        target = self.builtin_root / f"{normalized}.json"
        if not target.is_file():
            raise FileNotFoundError(f"Built-in content package is not installed: {extension_id}")
        target_snapshot = self._snapshot_file(target)
        payload = json.loads(target.read_text(encoding="utf-8"))
        manifest = ExtensionManifest.from_dict(payload.get("extension"), target)
        map_files = self._normalize_builtin_map_files(
            manifest, payload.get("map_files"),
        )
        payload = {
            "enabled": bool(enabled),
            "extension": manifest.to_dict(),
            "map_files": map_files,
        }
        try:
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            temporary.replace(target)
            self.rebuild()
        except Exception:
            self._restore_file(target, target_snapshot)
            raise

    def unregister_builtin(self, extension_id: str, *, force: bool = False) -> None:
        normalized = _identifier(extension_id, "extension id")
        if not force:
            self._refuse_enabled_dependents(normalized)
        target = self.builtin_root / f"{normalized}.json"
        target_snapshot = self._snapshot_file(target)
        try:
            target.unlink(missing_ok=True)
            self.rebuild()
        except Exception:
            self._restore_file(target, target_snapshot)
            raise

    def _refuse_enabled_dependents(self, extension_id: str) -> None:
        """Keep receipt-declared dependencies intact across built-in changes."""
        dependents: list[str] = []
        if self.receipt_root.is_dir():
            for path in sorted(self.receipt_root.glob("*.json")):
                try:
                    receipt = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not receipt.get("enabled", True):
                    continue
                requirements = receipt.get("requires", [])
                if not isinstance(requirements, list):
                    continue
                required_ids = {
                    re.split(r"==|>=", str(value).strip().lower(), maxsplit=1)[0]
                    for value in requirements
                }
                if extension_id in required_ids:
                    dependents.append(str(receipt.get("id", path.stem)))
        if dependents:
            raise ValueError(
                f"Content package '{extension_id}' is required by: "
                + ", ".join(sorted(dependents))
            )

    @staticmethod
    def _expected_map_path(manifest: ExtensionManifest) -> str:
        return f"scripts/ALLIN1/Maps/{manifest.extension_id}/maps.json"

    @staticmethod
    def _expected_builtin_map_root(manifest: ExtensionManifest) -> PurePosixPath:
        return PurePosixPath("scripts/ALLIN1/Maps") / manifest.extension_id

    @classmethod
    def _normalize_builtin_map_files(
        cls,
        manifest: ExtensionManifest,
        records: Iterable[Mapping[str, str]] | None,
    ) -> list[dict[str, str]]:
        """Validate the installer's immutable map authorization records."""
        supplied = list(records or ())
        if "world.maps" not in manifest.capabilities:
            if supplied:
                raise ValueError(
                    "Built-in map files require the world.maps capability"
                )
            return []
        if not supplied:
            return []
        expected_root = cls._expected_builtin_map_root(manifest)
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        for record in supplied:
            if not isinstance(record, Mapping) or set(record) != {"path", "sha256"}:
                return []
            try:
                relative = _safe_relative(
                    record.get("path"), "built-in map file path",
                )
            except ValueError:
                return []
            if (
                relative.parent.as_posix().casefold()
                != expected_root.as_posix().casefold()
                or not relative.name.casefold().endswith(".maps.json")
                or relative.name.casefold() == "maps.json"
            ):
                return []
            path_key = relative.as_posix().casefold()
            if path_key in seen:
                return []
            seen.add(path_key)
            digest = str(record.get("sha256", "")).strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                return []
            normalized.append({
                "path": relative.as_posix(),
                "sha256": digest,
            })
        return sorted(normalized, key=lambda item: item["path"].casefold())

    def _builtin_entries(self) -> list[_RegistryCandidate]:
        result: list[_RegistryCandidate] = []
        if not self.builtin_root.is_dir():
            return result
        for path in sorted(self.builtin_root.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            manifest = ExtensionManifest.from_dict(payload.get("extension"), path)
            enabled = bool(payload.get("enabled", True))
            map_files = self._normalize_builtin_map_files(
                manifest, payload.get("map_files"),
            )
            blocked = ""
            if "world.maps" in manifest.capabilities:
                expected_root = self._expected_builtin_map_root(manifest).as_posix()
                if not map_files:
                    blocked = (
                        "Map descriptors lack built-in hashes below: "
                        f"{expected_root}"
                    )
                elif enabled:
                    for record in map_files:
                        relative = record["path"]
                        installed = self.gta_path / Path(
                            *PurePosixPath(relative).parts
                        )
                        if (
                            not installed.is_file()
                            or self._file_sha256(installed) != record["sha256"]
                        ):
                            blocked = (
                                "Map descriptor failed its built-in hash: "
                                f"{relative}"
                            )
                            break
            result.append(_RegistryCandidate(
                manifest=manifest,
                requested_enabled=enabled,
                source="built-in",
                map_files=tuple(map_files),
                blocked_reason=blocked,
            ))
        return result

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _receipt_entries(self) -> list[_RegistryCandidate]:
        result: list[_RegistryCandidate] = []
        if not self.receipt_root.is_dir():
            return result
        for path in sorted(self.receipt_root.glob("*.json")):
            try:
                receipt = json.loads(path.read_text(encoding="utf-8"))
                extension = receipt.get("extension")
                if extension is None:
                    continue
                manifest = ExtensionManifest.from_dict(extension, path)
                if receipt.get("id") != manifest.extension_id:
                    raise ValueError("receipt id does not match its extension descriptor")
                enabled = bool(receipt.get("enabled", True))
                requirements = _content_requirements(receipt.get("requires", []))
                if any(
                    requirement.extension_id == manifest.extension_id
                    for requirement in requirements
                ):
                    raise ValueError("a content receipt may not depend on itself")
                records = {
                    str(item.get("destination", "")).replace("\\", "/").casefold(): item
                    for item in receipt.get("files", []) if isinstance(item, dict)
                }
                runtime_files: list[dict[str, str]] = []
                catalog_files: list[dict[str, str]] = []
                map_files: list[dict[str, str]] = []
                blocked = ""
                for assembly in manifest.runtime_assemblies:
                    relative = assembly.path.as_posix()
                    record = records.get(relative.casefold())
                    expected = str(record.get("sha256", "")) if record else ""
                    if not record or not re.fullmatch(r"[0-9a-f]{64}", expected):
                        blocked = blocked or (
                            f"Runtime assembly lacks a receipt hash: {relative}"
                        )
                        continue
                    runtime_files.append({"path": relative, "sha256": expected})
                    installed = self.gta_path / Path(*assembly.path.parts)
                    current = installed if enabled else installed.with_name(
                        installed.name + ".disabled"
                    )
                    if enabled and (
                        not current.is_file() or self._file_sha256(current) != expected
                    ):
                        blocked = blocked or (
                            f"Runtime assembly failed its receipt hash: {relative}"
                        )
                for catalog in manifest.gbay_catalogs:
                    relative = catalog.source.as_posix()
                    record = records.get(relative.casefold())
                    expected = str(record.get("sha256", "")) if record else ""
                    if not record or not re.fullmatch(r"[0-9a-f]{64}", expected):
                        blocked = blocked or (
                            f"GBAY catalog lacks a receipt hash: {relative}"
                        )
                        continue
                    catalog_files.append({"path": relative, "sha256": expected})
                    installed = self.gta_path / Path(*catalog.source.parts)
                    current = installed if enabled else installed.with_name(
                        installed.name + ".disabled"
                    )
                    if enabled and (
                        not current.is_file() or self._file_sha256(current) != expected
                    ):
                        blocked = blocked or (
                            f"GBAY catalog failed its receipt hash: {relative}"
                        )
                if "world.maps" in manifest.capabilities:
                    relative = (
                        f"scripts/ALLIN1/Maps/{manifest.extension_id}/maps.json"
                    )
                    record = records.get(relative.casefold())
                    expected = str(record.get("sha256", "")) if record else ""
                    if not record or not re.fullmatch(r"[0-9a-f]{64}", expected):
                        blocked = blocked or (
                            f"Map descriptor lacks a receipt hash: {relative}"
                        )
                    else:
                        map_files.append({"path": relative, "sha256": expected})
                        installed = self.gta_path / Path(*PurePosixPath(relative).parts)
                        current = installed if enabled else installed.with_name(
                            installed.name + ".disabled"
                        )
                        if enabled and (
                            not current.is_file()
                            or self._file_sha256(current) != expected
                        ):
                            blocked = blocked or (
                                f"Map descriptor failed its receipt hash: {relative}"
                            )
                result.append(_RegistryCandidate(
                    manifest=manifest,
                    requested_enabled=enabled,
                    source="package",
                    runtime_files=tuple(runtime_files),
                    catalog_files=tuple(catalog_files),
                    map_files=tuple(map_files),
                    blocked_reason=blocked,
                    requirements=requirements,
                ))
            except (OSError, ValueError, json.JSONDecodeError):
                # A corrupt receipt is already ignored by the package manager;
                # it must never authorize executable extension content.
                continue
        return result

    def inspect(self) -> dict[str, Any]:
        """Compute current authorization/settings without publishing files.

        UI inspection must not create registries, backups or preload caches.
        Rebuild uses this same computation so the read and write views cannot
        disagree about dependency and hash drift.
        """
        entries: dict[str, _RegistryCandidate] = {}
        for candidate in self._builtin_entries() + self._receipt_entries():
            extension_id = candidate.manifest.extension_id
            if extension_id in entries:
                raise ValueError(
                    f"Duplicate installed ALLIN1 content id: {extension_id}"
                )
            entries[extension_id] = candidate

        blocked_reasons = {
            extension_id: candidate.blocked_reason
            for extension_id, candidate in entries.items()
        }
        changed = True
        while changed:
            changed = False
            for extension_id, candidate in entries.items():
                if not candidate.requested_enabled or blocked_reasons[extension_id]:
                    continue
                for requirement in candidate.requirements:
                    dependency = entries.get(requirement.extension_id)
                    reason = ""
                    if dependency is None:
                        reason = (
                            "Required content package is missing: "
                            f"{requirement.normalized()}"
                        )
                    elif not dependency.requested_enabled:
                        reason = (
                            "Required content package is disabled: "
                            f"{requirement.extension_id}"
                        )
                    elif not requirement.accepts(dependency.manifest.version):
                        reason = (
                            f"Required content package version is incompatible: "
                            f"{requirement.normalized()} (installed "
                            f"{dependency.manifest.version})"
                        )
                    elif blocked_reasons[requirement.extension_id]:
                        reason = (
                            "Required content package is unavailable: "
                            f"{requirement.extension_id}"
                        )
                    if reason:
                        blocked_reasons[extension_id] = reason
                        changed = True
                        break

        normalized = []
        for extension_id in sorted(entries):
            candidate = entries[extension_id]
            blocked = blocked_reasons[extension_id]
            item = candidate.manifest.to_dict()
            item["enabled"] = candidate.requested_enabled and not blocked
            item["source"] = candidate.source
            item["settings"] = self.settings.effective(candidate.manifest)
            item["requires"] = [
                requirement.normalized()
                for requirement in candidate.requirements
            ]
            item["runtime_files"] = list(candidate.runtime_files)
            item["catalog_files"] = list(candidate.catalog_files)
            item["map_files"] = list(candidate.map_files)
            if blocked:
                item["blocked_reason"] = blocked
            normalized.append(item)
        payload = {
            "schema_version": REGISTRY_SCHEMA_VERSION,
            "api_version": EXTENSION_API_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "extensions": normalized,
        }
        return payload

    def installed_manifest(self, extension_id: str) -> ExtensionManifest:
        """Read an exact installed manifest without rebuilding registry state."""
        normalized = _identifier(extension_id, "extension id")
        entries = [item for item in self.inspect()["extensions"] if item["id"] == normalized]
        if not entries:
            raise KeyError(f"Content package is not installed: {normalized}")
        return ExtensionManifest.from_dict({key: value for key, value in entries[0].items() if key in _MANIFEST_FIELDS})

    def rebuild(self) -> dict[str, Any]:
        payload = self.inspect()
        self.state_root.mkdir(parents=True, exist_ok=True)
        temporary = self.registry_path.with_suffix(".json.tmp")
        backup = self.registry_path.with_suffix(".json.bak")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        if self.registry_path.exists():
            shutil.copy2(self.registry_path, backup)
        temporary.replace(self.registry_path)
        try:
            self._refresh_preload_manifest(payload)
        except (OSError, ValueError, TypeError):
            # The registry is the authorization boundary and has already been
            # committed. A cache-manifest failure may reduce startup
            # performance, but it must not roll back or misreport that state.
            log.warning(
                "Could not refresh ReactorV ALLIN1 preload manifest",
                exc_info=True,
            )
        return payload

    @staticmethod
    def _preload_entry_id(
        prefix: str, package_id: str, label: str, relative_path: str,
    ) -> str:
        readable = f"{prefix}.{package_id}.{label}".lower()
        if len(readable) <= 64 and _ID_PATTERN.fullmatch(readable):
            return readable
        digest = hashlib.sha256(
            f"{prefix}\0{package_id}\0{label}\0{relative_path}".encode("utf-8")
        ).hexdigest()[:20]
        return f"{prefix}.{package_id[:34]}.{digest}"[:64]

    def _refresh_preload_manifest(self, registry: Mapping[str, Any]) -> Path:
        """Publish a bounded cache request from receipt-authorized registry data."""
        entries: list[dict[str, Any]] = []
        aggregate_bytes = 0
        omitted = 0
        for static in PRELOAD_STATIC_ENTRIES:
            entry = dict(static)
            source = self.gta_path / Path(*PurePosixPath(entry["path"]).parts)
            source_bytes = source.stat().st_size if source.is_file() else 0
            if (
                not entry["required"]
                and (
                    source_bytes > entry["max_bytes"]
                    or (
                        aggregate_bytes + source_bytes
                        > PRELOAD_MAX_AGGREGATE_BYTES
                    )
                )
            ):
                omitted += 1
                continue
            entries.append(entry)
            aggregate_bytes += source_bytes
        dynamic: list[tuple[str, str, str, int, dict[str, Any]]] = []
        extensions = registry.get("extensions", [])
        if not isinstance(extensions, list):
            raise ValueError("extension registry has no extension list")
        for extension in extensions:
            if not isinstance(extension, Mapping) or not extension.get("enabled"):
                continue
            package_id = str(extension.get("id", "")).strip().lower()
            if not _ID_PATTERN.fullmatch(package_id):
                continue
            gbay = extension.get("gbay", {})
            raw_catalogs = gbay.get("catalogs", []) if isinstance(gbay, Mapping) else []
            catalog_labels: dict[str, str] = {}
            if isinstance(raw_catalogs, list):
                for catalog in raw_catalogs:
                    if not isinstance(catalog, Mapping):
                        continue
                    source = str(catalog.get("source", "")).replace("\\", "/")
                    label = str(catalog.get("id", "")).strip().lower()
                    if source and _ID_PATTERN.fullmatch(label):
                        catalog_labels[source.casefold()] = label

            for record in extension.get("catalog_files", []):
                if not isinstance(record, Mapping):
                    continue
                relative = str(record.get("path", "")).replace("\\", "/")
                label = catalog_labels.get(relative.casefold())
                if label is None:
                    continue
                try:
                    safe = _safe_relative(relative, "preload catalog path")
                except ValueError:
                    continue
                # The managed cache consumer deliberately accepts only the
                # scripts/ subtree. Catalogs elsewhere under GTA remain valid
                # package content, but are read normally instead of widening
                # the early handoff's least-privilege path boundary.
                if not safe.parts or safe.parts[0].casefold() != "scripts":
                    omitted += 1
                    continue
                source = self.gta_path / Path(*safe.parts)
                if (
                    not source.is_file()
                    or source.stat().st_size > PRELOAD_MAX_ENTRY_BYTES
                ):
                    continue
                dynamic.append((package_id, "catalog", safe.as_posix(),
                    source.stat().st_size, {
                    "id": self._preload_entry_id(
                        "catalog", package_id, label, safe.as_posix(),
                    ),
                    "path": safe.as_posix(),
                    "kind": "json",
                    "required": False,
                    "max_bytes": PRELOAD_MAX_ENTRY_BYTES,
                }))

            for record in extension.get("map_files", []):
                if not isinstance(record, Mapping):
                    continue
                relative = str(record.get("path", "")).replace("\\", "/")
                try:
                    safe = _safe_relative(relative, "preload map path")
                except ValueError:
                    continue
                if not safe.parts or safe.parts[0].casefold() != "scripts":
                    omitted += 1
                    continue
                source = self.gta_path / Path(*safe.parts)
                if (
                    not source.is_file()
                    or source.stat().st_size > PRELOAD_MAX_ENTRY_BYTES
                ):
                    continue
                label = safe.name.lower().removesuffix(".json")
                dynamic.append((package_id, "map", safe.as_posix(),
                    source.stat().st_size, {
                    "id": self._preload_entry_id(
                        "map", package_id, label, safe.as_posix(),
                    ),
                    "path": safe.as_posix(),
                    "kind": "json",
                    "required": False,
                    "max_bytes": PRELOAD_MAX_ENTRY_BYTES,
                }))

        seen_ids = {entry["id"].casefold() for entry in entries}
        seen_paths = {entry["path"].casefold() for entry in entries}
        for _package, _kind, _path, source_bytes, entry in sorted(
            dynamic, key=lambda item: (item[0], item[1], item[2].casefold())
        ):
            if (
                len(entries) >= PRELOAD_MAX_ENTRIES
                or aggregate_bytes + source_bytes > PRELOAD_MAX_AGGREGATE_BYTES
            ):
                omitted += 1
                continue
            if (
                entry["id"].casefold() in seen_ids
                or entry["path"].casefold() in seen_paths
            ):
                continue
            entries.append(entry)
            aggregate_bytes += source_bytes
            seen_ids.add(entry["id"].casefold())
            seen_paths.add(entry["path"].casefold())

        manifest = {
            "schema_version": PRELOAD_MANIFEST_SCHEMA_VERSION,
            "id": PRELOAD_MANIFEST_ID,
            "entries": entries,
        }
        encoded = (
            json.dumps(manifest, indent=2, sort_keys=False, allow_nan=False) + "\n"
        ).encode("utf-8")
        if len(encoded) > PRELOAD_MAX_MANIFEST_BYTES:
            raise ValueError("ALLIN1 preload manifest exceeds its size limit")
        self.preload_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.preload_manifest_path.with_suffix(".json.tmp")
        temporary.write_bytes(encoded)
        temporary.replace(self.preload_manifest_path)
        if omitted:
            log.warning(
                "Omitted %d authorized preload entries after the %d-entry cap",
                omitted, PRELOAD_MAX_ENTRIES,
            )
        return self.preload_manifest_path

    def read(self) -> dict[str, Any]:
        if not self.registry_path.is_file():
            return self.rebuild()
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid ALLIN1 extension registry: {self.registry_path}") from exc
        if (
            payload.get("schema_version") != REGISTRY_SCHEMA_VERSION
            or payload.get("api_version") != EXTENSION_API_VERSION
            or not isinstance(payload.get("extensions"), list)
        ):
            raise ValueError("Unsupported ALLIN1 extension registry")
        return payload

    def installed(self) -> list[dict[str, Any]]:
        # Launcher and automation callers need an authoritative view. Rebuild
        # from current built-ins/receipts so dependency or hash drift cannot be
        # hidden behind a previously generated registry snapshot.
        return list(self.rebuild()["extensions"])

    def set_settings(
        self, extension_id: str, values: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Atomically validate, persist, and publish package-owned settings."""
        entries = {
            entry.manifest.extension_id: entry.manifest
            for entry in self._builtin_entries() + self._receipt_entries()
        }
        normalized = _identifier(extension_id, "extension id")
        if normalized not in entries:
            raise KeyError(f"Content package is not installed: {normalized}")
        settings_snapshot = self._snapshot_file(self.settings.path)
        try:
            effective = self.settings.update(entries[normalized], values)
            self.rebuild()
        except Exception:
            self._restore_file(self.settings.path, settings_snapshot)
            raise
        return effective

    def set_setting(self, extension_id: str, key: str, value: Any) -> dict[str, Any]:
        return self.set_settings(extension_id, {key: value})
