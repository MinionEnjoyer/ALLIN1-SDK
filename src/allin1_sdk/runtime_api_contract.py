"""Strict, data-only contracts for ALLIN1 game-runtime APIs.

The contract describes the public surface a package may compile against.  It
does not load a DLL, import workspace code, or grant a package any authority.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


RUNTIME_API_CONTRACT_SCHEMA = 1
MAX_RUNTIME_API_CONTRACT_BYTES = 1024 * 1024
SUPPORTED_SYMBOL_KINDS = frozenset({
    "constant", "property", "method", "interface", "type",
})
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TYPE_NAME = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$"
)
_CAPABILITY = re.compile(r"^[a-z][a-z0-9._-]{1,95}$")
_WINDOWS_INVALID = frozenset('<>:"|?*')
_WINDOWS_DEVICES = frozenset({
    "con", "prn", "aux", "nul", "conin$", "conout$",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
})


def _safe_relative(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty relative path")
    normalized = value.replace("\\", "/")
    if normalized != normalized.strip():
        raise ValueError(f"{label} must not have outer whitespace")
    parts = normalized.split("/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"{label} must be relative and contain no traversal")
    for part in parts:
        if part.endswith((".", " ")) or any(
            character in _WINDOWS_INVALID or ord(character) < 32
            for character in part
        ):
            raise ValueError(f"{label} contains an invalid Windows path component")
        if part.split(".", 1)[0].casefold() in _WINDOWS_DEVICES:
            raise ValueError(f"{label} contains a reserved Windows device name")
    return path


def _required_text(data: Mapping[str, Any], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}.{key} must be non-empty text")
    if value != value.strip():
        raise ValueError(f"{label}.{key} must not have outer whitespace")
    return value


@dataclass(frozen=True)
class RuntimeApiParameter:
    name: str
    parameter_type: str
    optional: bool = False
    default: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "type": self.parameter_type,
        }
        if self.optional:
            result["optional"] = True
            result["default"] = self.default
        return result


def _type_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{label} must be non-empty C# type text")
    if len(value) > 160 or any(ord(character) < 32 for character in value):
        raise ValueError(f"{label} is invalid")
    if not re.fullmatch(r"(?:out |ref |in |params )?[A-Za-z_][A-Za-z0-9_.<>,?\[\] ]*", value):
        raise ValueError(f"{label} is invalid")
    return value


@dataclass(frozen=True)
class RuntimeApiSymbol:
    name: str
    kind: str
    capability: str | None = None
    requires: tuple[str, ...] = ()
    return_type: str | None = None
    parameters: tuple[RuntimeApiParameter, ...] = ()
    value: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
        }
        if self.capability is not None:
            result["capability"] = self.capability
        if self.requires:
            result["requires"] = list(self.requires)
        if self.return_type is not None:
            result["return_type"] = self.return_type
        if self.kind == "method":
            result["parameters"] = [item.to_dict() for item in self.parameters]
        if self.value is not None:
            result["value"] = self.value
        return result


@dataclass(frozen=True)
class RuntimeApiContract:
    contract_path: Path
    api_version: int
    assembly: PurePosixPath
    public_type: str
    source: PurePosixPath
    symbols: tuple[RuntimeApiSymbol, ...]

    @property
    def symbol_map(self) -> dict[str, RuntimeApiSymbol]:
        return {item.name: item for item in self.symbols}

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_API_CONTRACT_SCHEMA,
            "api_version": self.api_version,
            "assembly": self.assembly.as_posix(),
            "public_type": self.public_type,
            "source": self.source.as_posix(),
            "symbols": [item.to_dict() for item in self.symbols],
        }

    @classmethod
    def load(cls, source: str | Path) -> "RuntimeApiContract":
        path = Path(source).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Runtime API contract not found: {path}")
        if path.stat().st_size > MAX_RUNTIME_API_CONTRACT_BYTES:
            raise ValueError("Runtime API contract exceeds the size limit")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid runtime API contract JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Runtime API contract must be an object")
        allowed = {
            "schema_version", "api_version", "assembly", "public_type",
            "source", "symbols",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(
                "Unsupported runtime API contract field(s): "
                + ", ".join(sorted(unknown))
            )
        if data.get("schema_version") != RUNTIME_API_CONTRACT_SCHEMA:
            raise ValueError(
                f"runtime API contract schema_version must be "
                f"{RUNTIME_API_CONTRACT_SCHEMA}"
            )
        api_version = data.get("api_version")
        if isinstance(api_version, bool) or not isinstance(api_version, int) or api_version < 1:
            raise ValueError("runtime API contract api_version must be a positive integer")
        assembly = _safe_relative(data.get("assembly"), "assembly")
        if (
            not assembly.parts
            or assembly.parts[0].casefold() != "scripts"
            or assembly.suffix.casefold() != ".dll"
        ):
            raise ValueError("runtime API contract assembly must be a DLL below scripts/")
        public_type = _required_text(data, "public_type", "contract")
        if not _TYPE_NAME.fullmatch(public_type):
            raise ValueError("runtime API contract public_type must be fully qualified")
        source_path = _safe_relative(data.get("source"), "source")
        if source_path.suffix.casefold() != ".cs":
            raise ValueError("runtime API contract source must be a C# source file")
        raw_symbols = data.get("symbols")
        if not isinstance(raw_symbols, list) or not raw_symbols:
            raise ValueError("runtime API contract symbols must be a non-empty array")
        symbols: list[RuntimeApiSymbol] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_symbols, start=1):
            label = f"symbols[{index}]"
            if not isinstance(raw, dict):
                raise ValueError(f"{label} must be an object")
            unknown = set(raw) - {
                "name", "kind", "capability", "requires", "return_type",
                "parameters", "value",
            }
            if unknown:
                raise ValueError(
                    f"Unsupported {label} field(s): " + ", ".join(sorted(unknown))
                )
            name = _required_text(raw, "name", label)
            if not _IDENTIFIER.fullmatch(name):
                raise ValueError(f"{label}.name must be a C# identifier")
            key = name.casefold()
            if key in seen:
                raise ValueError("runtime API contract contains duplicate symbol names")
            seen.add(key)
            kind = _required_text(raw, "kind", label).casefold()
            if kind not in SUPPORTED_SYMBOL_KINDS:
                raise ValueError(f"{label}.kind is not supported")
            capability_value = raw.get("capability")
            capability = None
            if capability_value is not None:
                if not isinstance(capability_value, str):
                    raise ValueError(f"{label}.capability must be text")
                capability = capability_value.strip().casefold()
                if not _CAPABILITY.fullmatch(capability):
                    raise ValueError(f"{label}.capability is invalid")
            raw_requires = raw.get("requires", [])
            if not isinstance(raw_requires, list) or not all(
                isinstance(item, str) and _IDENTIFIER.fullmatch(item)
                for item in raw_requires
            ):
                raise ValueError(f"{label}.requires must contain C# symbol names")
            if len(raw_requires) != len(set(raw_requires)):
                raise ValueError(f"{label}.requires contains duplicates")
            requires = tuple(raw_requires)
            return_type_value = raw.get("return_type")
            return_type = None
            if kind in {"constant", "property", "method"}:
                return_type = _type_text(
                    return_type_value, f"{label}.return_type",
                )
            elif return_type_value is not None:
                raise ValueError(
                    f"{label}.return_type is valid only for constants, properties, and methods"
                )
            raw_parameters = raw.get("parameters")
            parameters: list[RuntimeApiParameter] = []
            if kind == "method":
                if not isinstance(raw_parameters, list):
                    raise ValueError(f"{label}.parameters must be an array")
                parameter_names: set[str] = set()
                for parameter_index, parameter in enumerate(raw_parameters, start=1):
                    parameter_label = f"{label}.parameters[{parameter_index}]"
                    if not isinstance(parameter, dict):
                        raise ValueError(f"{parameter_label} must be an object")
                    unknown_parameter = set(parameter) - {
                        "name", "type", "optional", "default",
                    }
                    if unknown_parameter:
                        raise ValueError(
                            f"Unsupported {parameter_label} field(s): "
                            + ", ".join(sorted(unknown_parameter))
                        )
                    parameter_name = _required_text(
                        parameter, "name", parameter_label,
                    )
                    if not _IDENTIFIER.fullmatch(parameter_name):
                        raise ValueError(f"{parameter_label}.name is invalid")
                    if parameter_name in parameter_names:
                        raise ValueError(f"{label}.parameters contains duplicate names")
                    parameter_names.add(parameter_name)
                    parameter_type = _type_text(
                        parameter.get("type"), f"{parameter_label}.type",
                    )
                    optional = parameter.get("optional", False)
                    if not isinstance(optional, bool):
                        raise ValueError(f"{parameter_label}.optional must be boolean")
                    default = parameter.get("default")
                    if optional:
                        if not isinstance(default, str) or not default.strip():
                            raise ValueError(
                                f"{parameter_label}.default is required for an optional parameter"
                            )
                    elif default is not None:
                        raise ValueError(
                            f"{parameter_label}.default requires optional=true"
                        )
                    parameters.append(RuntimeApiParameter(
                        parameter_name, parameter_type, optional, default,
                    ))
            elif raw_parameters is not None:
                raise ValueError(f"{label}.parameters is valid only for methods")
            value_data = raw.get("value")
            value_text = None
            if kind == "constant":
                if not isinstance(value_data, str) or not value_data.strip():
                    raise ValueError(f"{label}.value is required for constants")
                value_text = value_data.strip()
            elif value_data is not None:
                raise ValueError(f"{label}.value is valid only for constants")
            symbols.append(RuntimeApiSymbol(
                name, kind, capability, requires, return_type,
                tuple(parameters), value_text,
            ))
        symbol_names = {item.name for item in symbols}
        for symbol in symbols:
            missing = set(symbol.requires) - symbol_names
            if missing:
                raise ValueError(
                    f"runtime API symbol {symbol.name} requires unknown symbols: "
                    + ", ".join(sorted(missing))
                )
        return cls(
            path, int(api_version), assembly, public_type, source_path,
            tuple(symbols),
        )


__all__ = [
    "MAX_RUNTIME_API_CONTRACT_BYTES",
    "RUNTIME_API_CONTRACT_SCHEMA",
    "RuntimeApiContract",
    "RuntimeApiParameter",
    "RuntimeApiSymbol",
    "SUPPORTED_SYMBOL_KINDS",
]
