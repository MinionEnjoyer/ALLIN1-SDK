# Frozen pre-schema-3 envelope reader for downgrade/compatibility regression.
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
