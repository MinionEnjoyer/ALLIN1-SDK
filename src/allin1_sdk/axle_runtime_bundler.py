"""Capability-driven, cross-edition axle runtime bundle planning.

The module publishes *staged packages only*.  It does not install into GTA V,
download tools, compile ASIs, execute unapproved converters, or claim that a
packaged target passed an in-game acceptance test.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import struct
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence

from allin1_sdk.axle_configurator import (
    AXLE_SCHEMA_VERSION,
    CANONICAL_WHEEL_PAIRS,
    EXPORT_FIVEM_RUNTIME,
    AxleConfiguration,
    fivem_client_lua,
    fivem_server_lua,
    joaat_hex,
    validate_axle_configuration,
)


BUNDLE_SCHEMA_VERSION = 1
RUNTIME_CONTRACT_VERSION = 1

TARGET_FIVEM_LEGACY = "fivem-legacy"
TARGET_FIVEM_ENHANCED = "fivem-enhanced"
TARGET_STORY_LEGACY = "story-legacy"
TARGET_STORY_ENHANCED = "story-enhanced"
TARGET_IDS = (
    TARGET_FIVEM_LEGACY,
    TARGET_FIVEM_ENHANCED,
    TARGET_STORY_LEGACY,
    TARGET_STORY_ENHANCED,
)

FIVEM_RUNTIME_NAME = "allin1-vehicle-workbench-axles"
STORY_RUNTIME_NAME = "VehicleWorkbenchAxles"
DEFAULT_RUNTIME_VERSION = "1.0.0"

ACCEPTANCE_PENDING = "awaiting_in_game_validation"
STATUS_READY = "ready"
STATUS_OMITTED = "omitted"

_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+]([0-9A-Za-z.-]+))?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")

STORY_RUNTIME_DESCRIPTOR_EXPORT = "VehicleWorkbenchAxles_GetDescriptor"
STORY_RUNTIME_PROFILE_EXPORT = "VehicleWorkbenchAxles_HasValidatedProfile"
STORY_RUNTIME_REQUIRED_EXPORTS = (
    STORY_RUNTIME_DESCRIPTOR_EXPORT,
    STORY_RUNTIME_PROFILE_EXPORT,
)
STORY_RUNTIME_RECEIPT_SCHEMA_VERSION = 1
_MAX_STORY_RUNTIME_BYTES = 64 * 1024 * 1024
_MAX_STORY_RECEIPT_BYTES = 2 * 1024 * 1024
_REQUIRED_STORY_ACCEPTANCE_TESTS = frozenset({
    "front_steer",
    "selective_drive",
    "rear_steer",
    "unrelated_flags_preserved",
    "repair_reapplication",
    "unsupported_build_fail_closed",
    "online_session_guard",
})

_THIRD_PARTY_BINARY_NAMES = frozenset({
    "scripthookv.dll", "scripthookv.net.dll", "dinput8.dll",
    "openiv.asi", "openrpf.asi", "alchemist.exe",
})

# Mapping is semantic, not spatial/ordinal.  A target may replace this rule as
# its runtime evolves without changing authoring files.
_CANONICAL_WHEEL_SEQUENCE = tuple(
    bone
    for _role, left, right in CANONICAL_WHEEL_PAIRS
    for bone in (left, right)
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validated_version(value: str, label: str = "Version") -> str:
    text = str(value).strip()
    if not _SEMVER.fullmatch(text):
        raise ValueError(f"{label} must use semantic major.minor.patch form")
    return text


def _version_key(value: str) -> tuple[int, int, int, int, str]:
    match = _SEMVER.fullmatch(_validated_version(value))
    assert match is not None
    # Stable releases sort after prerelease/build-suffixed versions at the same
    # numeric triplet. This is sufficient for dependency selection without
    # pretending to be a complete package-manager implementation.
    suffix = match.group(4) or ""
    return (
        int(match.group(1)), int(match.group(2)), int(match.group(3)),
        1 if not suffix else 0, suffix,
    )


def _safe_id(value: str, label: str) -> str:
    text = str(value).strip().casefold()
    if not _SAFE_ID.fullmatch(text):
        raise ValueError(
            f"{label} must use 1-96 lowercase letters, numbers, dots, dashes, or underscores"
        )
    return text


def _model_hash(value: int | str) -> str:
    if isinstance(value, bool):
        raise ValueError("Model hash must be an unsigned 32-bit integer")
    if isinstance(value, str):
        text = value.strip().casefold()
        try:
            number = int(text, 16) if text.startswith("0x") else int(text, 10)
        except ValueError as exc:
            raise ValueError("Model hash must be an unsigned 32-bit integer") from exc
    elif isinstance(value, int):
        number = value
    else:
        raise ValueError("Model hash must be an unsigned 32-bit integer")
    if not 0 <= number <= 0xFFFFFFFF:
        raise ValueError("Model hash must be an unsigned 32-bit integer")
    return f"0x{number:08X}"


def _relative_file(path: str, label: str) -> str:
    text = str(path).replace("\\", "/").strip()
    parts = text.split("/")
    if (
        not text or text.startswith("/") or ":" in parts[0]
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError(f"{label} must be a safe relative path")
    return text


def _safe_regular_file(
    path: Path,
    label: str,
    *,
    maximum_bytes: int,
    suffix: str | None = None,
) -> Path:
    resolved = Path(path).expanduser().resolve(strict=False)
    if not resolved.is_file() or resolved.is_symlink():
        raise ValueError(f"{label} is missing or unsafe")
    if suffix is not None and resolved.suffix.casefold() != suffix.casefold():
        raise ValueError(f"{label} must use the {suffix} extension")
    size = resolved.stat().st_size
    if size < 1 or size > maximum_bytes:
        raise ValueError(f"{label} is empty or exceeds its guarded size limit")
    return resolved


def _bounded_json_object(path: Path, label: str, maximum_bytes: int) -> dict[str, Any]:
    source = _safe_regular_file(path, label, maximum_bytes=maximum_bytes)
    try:
        payload = json.loads(source.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} root must be an object")
    return payload


@dataclass(frozen=True)
class PeExportEvidence:
    architecture: str
    machine: str
    pe_format: str
    is_dll: bool
    file_size: int
    sha256: str
    exports: tuple[str, ...]
    required_exports: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_story_runtime_binary(path: Path) -> PeExportEvidence:
    """Parse enough PE metadata to prove an x64 DLL and executable exports.

    This is deliberately independent of filename text or embedded ASCII. It is
    not a signature/authenticode verifier; release trust comes from the pinned
    receipt hash and package-eligibility receipt validated separately.
    """
    source = _safe_regular_file(
        path, "Story runtime binary", maximum_bytes=_MAX_STORY_RUNTIME_BYTES,
        suffix=".asi",
    )
    data = source.read_bytes()

    def unpack(fmt: str, offset: int, label: str) -> tuple[Any, ...]:
        size = struct.calcsize(fmt)
        if offset < 0 or offset + size > len(data):
            raise ValueError(f"Story runtime PE has a truncated {label}")
        return struct.unpack_from(fmt, data, offset)

    if len(data) < 0x40 or data[:2] != b"MZ":
        raise ValueError("Story runtime binary is not a PE file (missing DOS header)")
    pe_offset = unpack("<I", 0x3C, "PE offset")[0]
    if pe_offset < 0x40 or pe_offset + 24 > len(data) \
            or data[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise ValueError("Story runtime binary has an invalid PE signature")
    coff = pe_offset + 4
    machine, section_count, _timestamp, _symbols, _symbol_count, optional_size, characteristics = \
        unpack("<HHIIIHH", coff, "COFF header")
    if machine != 0x8664:
        raise ValueError("Story runtime binary must target x64 AMD64")
    if section_count < 1 or section_count > 96:
        raise ValueError("Story runtime PE has an invalid section count")
    if not characteristics & 0x2000:
        raise ValueError("Story runtime PE is not marked as a DLL")
    optional = coff + 20
    if optional_size < 120 or optional + optional_size > len(data):
        raise ValueError("Story runtime PE has a truncated optional header")
    if unpack("<H", optional, "optional header magic")[0] != 0x20B:
        raise ValueError("Story runtime binary must use PE32+ x64 format")
    number_of_directories = unpack("<I", optional + 108, "data-directory count")[0]
    if number_of_directories < 1:
        raise ValueError("Story runtime PE has no export data directory")
    export_rva, export_size = unpack("<II", optional + 112, "export directory")
    if export_rva == 0 or export_size < 40:
        raise ValueError("Story runtime PE has no usable export table")

    section_table = optional + optional_size
    sections: list[tuple[int, int, int, int, int]] = []
    for index in range(section_count):
        offset = section_table + index * 40
        _name = unpack("<8s", offset, "section name")[0]
        virtual_size, virtual_address, raw_size, raw_offset = unpack(
            "<IIII", offset + 8, "section layout",
        )
        section_flags = unpack("<I", offset + 36, "section flags")[0]
        if raw_offset + raw_size > len(data):
            raise ValueError("Story runtime PE section exceeds the file boundary")
        sections.append((
            virtual_address, max(virtual_size, raw_size), raw_offset,
            raw_size, section_flags,
        ))

    def rva_location(rva: int, size: int, label: str) -> tuple[int, int]:
        for virtual_address, span, raw_offset, raw_size, flags in sections:
            if virtual_address <= rva and rva + size <= virtual_address + span:
                delta = rva - virtual_address
                if delta + size > raw_size:
                    break
                return raw_offset + delta, flags
        raise ValueError(f"Story runtime PE {label} points outside backed sections")

    export_offset, _export_flags = rva_location(
        export_rva, export_size, "export directory",
    )
    number_functions = unpack("<I", export_offset + 20, "export function count")[0]
    number_names = unpack("<I", export_offset + 24, "export name count")[0]
    functions_rva = unpack("<I", export_offset + 28, "export function table")[0]
    names_rva = unpack("<I", export_offset + 32, "export name table")[0]
    ordinals_rva = unpack("<I", export_offset + 36, "export ordinal table")[0]
    if number_names < len(STORY_RUNTIME_REQUIRED_EXPORTS) or number_names > 4096 \
            or number_functions < number_names or number_functions > 4096:
        raise ValueError("Story runtime PE has an invalid export count")
    functions_offset, _ = rva_location(
        functions_rva, number_functions * 4, "export function table",
    )
    names_offset, _ = rva_location(
        names_rva, number_names * 4, "export name table",
    )
    ordinals_offset, _ = rva_location(
        ordinals_rva, number_names * 2, "export ordinal table",
    )

    exports: dict[str, int] = {}
    for index in range(number_names):
        name_rva = unpack("<I", names_offset + index * 4, "export name RVA")[0]
        name_offset, _ = rva_location(name_rva, 1, "export name")
        terminator = data.find(b"\0", name_offset, min(len(data), name_offset + 256))
        if terminator < 0:
            raise ValueError("Story runtime PE contains an unterminated export name")
        try:
            name = data[name_offset:terminator].decode("ascii")
        except UnicodeDecodeError as exc:
            raise ValueError("Story runtime PE export names must be ASCII") from exc
        ordinal = unpack("<H", ordinals_offset + index * 2, "export ordinal")[0]
        if ordinal >= number_functions:
            raise ValueError("Story runtime PE export ordinal is out of range")
        function_rva = unpack(
            "<I", functions_offset + ordinal * 4, "export function RVA",
        )[0]
        if export_rva <= function_rva < export_rva + export_size:
            raise ValueError("Forwarded Story runtime exports are not accepted")
        _function_offset, function_flags = rva_location(
            function_rva, 1, f"exported function {name}",
        )
        if not function_flags & 0x20000000:
            raise ValueError(f"Story runtime export {name} is not executable")
        if name in exports:
            raise ValueError("Story runtime PE contains duplicate export names")
        exports[name] = function_rva

    missing = sorted(set(STORY_RUNTIME_REQUIRED_EXPORTS) - set(exports))
    if missing:
        raise ValueError(
            "Story runtime PE is missing required exports: " + ", ".join(missing)
        )
    return PeExportEvidence(
        architecture="x64",
        machine="AMD64",
        pe_format="PE32+",
        is_dll=True,
        file_size=len(data),
        sha256=_sha256_file(source),
        exports=tuple(sorted(exports)),
        required_exports=STORY_RUNTIME_REQUIRED_EXPORTS,
    )


@dataclass(frozen=True)
class TargetCapabilities:
    target_id: str
    family: str
    edition: str
    supports_runtime_wheel_flags: bool
    requires_asset_conversion: bool
    requires_scripthookv: bool
    supports_current_axle_schema: bool
    maximum_axle_schema: int
    supports_selective_steering: bool
    supports_selective_drive: bool
    runtime_implementation_version: str
    supported_game_builds: tuple[str, ...]
    minimum_physical_axles: int = 2
    maximum_physical_axles: int = 5
    acceptance_status: str = ACCEPTANCE_PENDING
    published_supported: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


TARGET_CAPABILITIES: Mapping[str, TargetCapabilities] = {
    TARGET_FIVEM_LEGACY: TargetCapabilities(
        TARGET_FIVEM_LEGACY, "fivem", "legacy", True, False, False,
        True, AXLE_SCHEMA_VERSION, True, True, DEFAULT_RUNTIME_VERSION,
        ("fivem-current",),
    ),
    TARGET_FIVEM_ENHANCED: TargetCapabilities(
        TARGET_FIVEM_ENHANCED, "fivem", "enhanced", True, True, False,
        True, AXLE_SCHEMA_VERSION, True, True, DEFAULT_RUNTIME_VERSION,
        ("fivem-enhanced-current",),
    ),
    TARGET_STORY_LEGACY: TargetCapabilities(
        TARGET_STORY_LEGACY, "story", "legacy", True, False, True,
        True, AXLE_SCHEMA_VERSION, True, True, DEFAULT_RUNTIME_VERSION,
        (),
    ),
    TARGET_STORY_ENHANCED: TargetCapabilities(
        TARGET_STORY_ENHANCED, "story", "enhanced", True, True, True,
        True, AXLE_SCHEMA_VERSION, True, True, DEFAULT_RUNTIME_VERSION,
        (),
    ),
}


def target_capabilities(target_id: str) -> TargetCapabilities:
    normalized = str(target_id).strip().casefold()
    try:
        return TARGET_CAPABILITIES[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown axle bundle target: {target_id}") from exc


@dataclass(frozen=True)
class DependencyDeclaration:
    name: str
    version: str | None
    source_url: str
    license_name: str
    bundled: bool = False
    redistribution_allowed: bool = False
    binary_path: Path | None = None
    purpose: str = "runtime dependency"

    def validate(self) -> "DependencyDeclaration":
        name = str(self.name).strip()
        if not name or any(character in name for character in "\r\n"):
            raise ValueError("Dependency name must be a non-empty single line")
        if self.version is not None:
            _validated_version(self.version, f"{name} version")
        if not str(self.source_url).startswith(("https://", "http://")):
            raise ValueError(f"{name} must provide an official HTTP(S) source URL")
        if not str(self.license_name).strip():
            raise ValueError(f"{name} must declare its license or redistribution terms")
        path = None
        if self.binary_path is not None:
            path = Path(self.binary_path).expanduser().resolve(strict=False)
            if not path.is_file() or path.is_symlink():
                raise ValueError(f"Dependency binary is missing or unsafe: {name}")
        if self.bundled and (path is None or not self.redistribution_allowed):
            raise ValueError(
                f"Dependency {name} cannot be bundled without a file and confirmed redistribution rights"
            )
        if not self.bundled and path is not None:
            raise ValueError(
                f"Dependency {name} has a binary path but is declared link-only"
            )
        return self

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "name": self.name,
            "version": self.version,
            "source_url": self.source_url,
            "license": self.license_name,
            "purpose": self.purpose,
            "bundled": self.bundled,
            "redistribution_allowed": self.redistribution_allowed,
            "sha256": _sha256_file(Path(self.binary_path)) if self.bundled else None,
        }


SCRIPHOOK_DEPENDENCY = DependencyDeclaration(
    name="ScriptHookV",
    version=None,
    source_url="https://www.dev-c.com/gtav/scripthookv/",
    license_name="Third-party terms; not redistributed by this bundle",
    bundled=False,
    redistribution_allowed=False,
    purpose="Story Mode ASI host",
)

SCRIPHOOK_ENHANCED_DEPENDENCY = DependencyDeclaration(
    name="ScriptHookV Enhanced",
    version=None,
    source_url="https://www.dev-c.com/post/scripthookv-enhanced",
    license_name="Third-party terms; not redistributed by this bundle",
    bundled=False,
    redistribution_allowed=False,
    purpose="Enhanced Story Mode ASI host",
)

ALCHEMIST_DEPENDENCY = DependencyDeclaration(
    name="Cfx Alchemist",
    version=None,
    source_url="https://docs.fivem.net/docs/alchemist/",
    license_name="Third-party terms; user-provided tool, not redistributed",
    bundled=False,
    redistribution_allowed=False,
    purpose="FiveM Enhanced Gen8-to-Gen9 asset conversion",
)


def _validated_game_builds(values: Iterable[Any], label: str) -> tuple[str, ...]:
    raw = tuple(values)
    if any(not isinstance(value, str) for value in raw):
        raise ValueError(f"{label} game build identifiers must be strings")
    builds = tuple(value.strip() for value in raw)
    if not builds or len(builds) != len(set(builds)):
        raise ValueError(f"{label} must contain unique supported game builds")
    if any(
        not value or len(value) > 96 or any(character in value for character in "\r\n")
        for value in builds
    ):
        raise ValueError(f"{label} contains an invalid game build identifier")
    return builds


def _required_json_bool(payload: Mapping[str, Any], key: str, label: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{label} {key} must be a boolean")
    return value


def _required_json_int(payload: Mapping[str, Any], key: str, label: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} {key} must be an integer")
    return value


@dataclass(frozen=True)
class StoryRuntimeValidationReceipt:
    schema_version: int
    receipt_id: str
    profile_id: str
    runtime_name: str
    target_id: str
    runtime_version: str
    binary_sha256: str
    binary_architecture: str
    supported_game_builds: tuple[str, ...]
    maximum_axle_schema: int
    descriptor_abi_version: int
    required_exports: tuple[str, ...]
    validated_profile_export_result: bool
    acceptance_tests: tuple[tuple[str, str], ...]
    validation_authority: str
    accepted_at: str
    package_eligible: bool
    redistribution_allowed: bool
    license_name: str

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StoryRuntimeValidationReceipt":
        tests = payload.get("acceptance_tests")
        builds = payload.get("supported_game_builds")
        exports = payload.get("required_exports")
        if not isinstance(tests, Mapping):
            raise ValueError("Story runtime receipt acceptance_tests must be an object")
        if not isinstance(builds, list) or not isinstance(exports, list):
            raise ValueError(
                "Story runtime receipt builds and required_exports must be arrays"
            )
        if any(not isinstance(value, str) for value in exports):
            raise ValueError("Story runtime receipt exports must be strings")
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in tests.items()
        ):
            raise ValueError("Story runtime receipt acceptance results must be strings")
        return cls(
            schema_version=_required_json_int(
                payload, "schema_version", "Story runtime receipt",
            ),
            receipt_id=str(payload.get("receipt_id", "")),
            profile_id=str(payload.get("profile_id", "")),
            runtime_name=str(payload.get("runtime_name", "")),
            target_id=str(payload.get("target_id", "")).casefold(),
            runtime_version=str(payload.get("runtime_version", "")),
            binary_sha256=str(payload.get("binary_sha256", "")).casefold(),
            binary_architecture=str(payload.get("binary_architecture", "")),
            supported_game_builds=tuple(str(value) for value in builds),
            maximum_axle_schema=_required_json_int(
                payload, "maximum_axle_schema", "Story runtime receipt",
            ),
            descriptor_abi_version=_required_json_int(
                payload, "descriptor_abi_version", "Story runtime receipt",
            ),
            required_exports=tuple(str(value) for value in exports),
            validated_profile_export_result=_required_json_bool(
                payload, "validated_profile_export_result", "Story runtime receipt",
            ),
            acceptance_tests=tuple(
                sorted((str(key), str(value)) for key, value in tests.items())
            ),
            validation_authority=str(payload.get("validation_authority", "")),
            accepted_at=str(payload.get("accepted_at", "")),
            package_eligible=_required_json_bool(
                payload, "package_eligible", "Story runtime receipt",
            ),
            redistribution_allowed=_required_json_bool(
                payload, "redistribution_allowed", "Story runtime receipt",
            ),
            license_name=str(payload.get("license", "")),
        )

    @classmethod
    def load(cls, path: Path) -> "StoryRuntimeValidationReceipt":
        return cls.from_dict(_bounded_json_object(
            path, "Story runtime validation receipt", _MAX_STORY_RECEIPT_BYTES,
        ))

    def validate_against(
        self,
        *,
        profile_id: str,
        target_id: str,
        runtime_version: str,
        binary_evidence: PeExportEvidence,
        supported_game_builds: tuple[str, ...],
        maximum_schema_version: int,
        redistribution_allowed: bool,
        license_name: str,
    ) -> "StoryRuntimeValidationReceipt":
        if self.schema_version != STORY_RUNTIME_RECEIPT_SCHEMA_VERSION:
            raise ValueError("Story runtime receipt schema is unsupported")
        _safe_id(self.receipt_id, "Story runtime receipt id")
        if not _PROFILE_ID.fullmatch(self.profile_id.casefold()) \
                or self.profile_id.casefold() != profile_id.casefold():
            raise ValueError("Story runtime receipt profile id does not match")
        if self.runtime_name.casefold() != STORY_RUNTIME_NAME.casefold():
            raise ValueError("Story runtime receipt names a different runtime")
        if self.target_id != target_id:
            raise ValueError("Story runtime receipt targets a different edition")
        if _validated_version(self.runtime_version, "Receipt runtime version") \
                != runtime_version:
            raise ValueError("Story runtime receipt version does not match")
        if not _SHA256.fullmatch(self.binary_sha256) \
                or self.binary_sha256 != binary_evidence.sha256:
            raise ValueError("Story runtime receipt binary checksum does not match")
        if self.binary_architecture.casefold() != "x64" \
                or binary_evidence.architecture != "x64":
            raise ValueError("Story runtime receipt must attest an x64 binary")
        receipt_builds = _validated_game_builds(
            self.supported_game_builds, "Story runtime receipt",
        )
        if receipt_builds != supported_game_builds:
            raise ValueError("Story runtime receipt game builds do not match the profile")
        if self.maximum_axle_schema != maximum_schema_version \
                or maximum_schema_version < AXLE_SCHEMA_VERSION:
            raise ValueError("Story runtime receipt axle schema does not match")
        if self.descriptor_abi_version != RUNTIME_CONTRACT_VERSION:
            raise ValueError("Story runtime receipt descriptor ABI is unsupported")
        if not set(STORY_RUNTIME_REQUIRED_EXPORTS).issubset(self.required_exports) \
                or not set(self.required_exports).issubset(binary_evidence.exports):
            raise ValueError("Story runtime receipt export evidence does not match the PE")
        if len(self.required_exports) != len(set(self.required_exports)):
            raise ValueError("Story runtime receipt contains duplicate exports")
        if not self.validated_profile_export_result:
            raise ValueError(
                "Story runtime receipt did not verify an enabled build profile export"
            )
        acceptance = dict(self.acceptance_tests)
        missing = sorted(_REQUIRED_STORY_ACCEPTANCE_TESTS - set(acceptance))
        failed = sorted(
            key for key in _REQUIRED_STORY_ACCEPTANCE_TESTS
            if acceptance.get(key) != "passed"
        )
        if missing or failed:
            raise ValueError(
                "Story runtime receipt has incomplete or failed acceptance tests"
            )
        authority = self.validation_authority.strip()
        if not authority or len(authority) > 160 \
                or any(character in authority for character in "\r\n"):
            raise ValueError("Story runtime receipt validation authority is invalid")
        try:
            accepted = datetime.fromisoformat(self.accepted_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Story runtime receipt accepted_at is not ISO-8601") from exc
        if accepted.tzinfo is None:
            raise ValueError("Story runtime receipt accepted_at must include a timezone")
        if not self.package_eligible:
            raise ValueError("Story runtime receipt is not package eligible")
        if not self.redistribution_allowed or not redistribution_allowed:
            raise ValueError("Story runtime receipt does not confirm redistribution rights")
        if not self.license_name.strip() or self.license_name != license_name:
            raise ValueError("Story runtime receipt license does not match the profile")
        return self

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["acceptance_tests"] = dict(self.acceptance_tests)
        payload["license"] = payload.pop("license_name")
        return payload


@dataclass(frozen=True)
class RuntimeDependency:
    name: str
    version: str
    maximum_schema_version: int
    target_id: str
    supported_game_builds: tuple[str, ...]
    configuration_destination: str
    binary_path: Path | None = None
    binary_sha256: str | None = None
    profile_id: str | None = None
    package_eligible: bool = False
    validation_receipt_path: Path | None = None
    validation_receipt_sha256: str | None = None
    binary_evidence: PeExportEvidence | None = None
    license_name: str = "GPL-3.0-or-later"
    redistribution_allowed: bool = True

    def validate(self) -> "RuntimeDependency":
        _safe_id(self.name, "Runtime name")
        version = _validated_version(self.version, "Runtime version")
        capability = target_capabilities(self.target_id)
        if self.maximum_schema_version < 1:
            raise ValueError("Runtime maximum schema version must be positive")
        _relative_file(self.configuration_destination, "Configuration destination")
        if self.binary_path is not None:
            path = _safe_regular_file(
                self.binary_path, "Runtime binary",
                maximum_bytes=_MAX_STORY_RUNTIME_BYTES, suffix=".asi",
            )
            actual = _sha256_file(path)
            if not isinstance(self.binary_sha256, str) \
                    or not _SHA256.fullmatch(self.binary_sha256.casefold()):
                raise ValueError("Story runtime profile must pin the binary SHA-256")
            if self.binary_sha256.casefold() != actual:
                raise ValueError("Runtime binary checksum does not match its build profile")
            if capability.family != "story":
                raise ValueError("A binary runtime cannot be placed in a FiveM target")
            if not isinstance(self.redistribution_allowed, bool) \
                    or not self.redistribution_allowed:
                raise ValueError("Runtime binary redistribution rights are not confirmed")
            if not isinstance(self.package_eligible, bool) or not self.package_eligible:
                raise ValueError("Story runtime profile is not package eligible")
            if not self.profile_id or not _PROFILE_ID.fullmatch(self.profile_id.casefold()):
                raise ValueError("Story runtime profile id is missing or invalid")
            builds = _validated_game_builds(
                self.supported_game_builds, "Story runtime profile",
            )
            if self.validation_receipt_path is None:
                raise ValueError("Story runtime profile requires a validation receipt")
            receipt_path = _safe_regular_file(
                self.validation_receipt_path, "Story runtime validation receipt",
                maximum_bytes=_MAX_STORY_RECEIPT_BYTES, suffix=".json",
            )
            receipt_sha = _sha256_file(receipt_path)
            if not isinstance(self.validation_receipt_sha256, str) \
                    or not _SHA256.fullmatch(self.validation_receipt_sha256.casefold()) \
                    or self.validation_receipt_sha256.casefold() != receipt_sha:
                raise ValueError("Story runtime validation receipt checksum does not match")
            evidence = inspect_story_runtime_binary(path)
            if self.binary_evidence is not None and self.binary_evidence != evidence:
                raise ValueError("Story runtime PE evidence changed after profile creation")
            receipt = StoryRuntimeValidationReceipt.load(receipt_path)
            receipt.validate_against(
                profile_id=self.profile_id,
                target_id=capability.target_id,
                runtime_version=version,
                binary_evidence=evidence,
                supported_game_builds=builds,
                maximum_schema_version=self.maximum_schema_version,
                redistribution_allowed=self.redistribution_allowed,
                license_name=self.license_name,
            )
        elif capability.family == "story":
            raise ValueError("Story runtime dependency requires a validated ASI binary")
        return self

    def checksum(self) -> str | None:
        if self.binary_path is None:
            return None
        return self.binary_sha256 or _sha256_file(Path(self.binary_path))

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        evidence = (
            inspect_story_runtime_binary(Path(self.binary_path))
            if self.binary_path is not None else None
        )
        return {
            "name": self.name,
            "version": self.version,
            "maximum_schema_version": self.maximum_schema_version,
            "target_id": self.target_id,
            "supported_game_builds": list(self.supported_game_builds),
            "configuration_destination": self.configuration_destination,
            "binary_sha256": self.checksum(),
            "profile_id": self.profile_id,
            "package_eligible": self.package_eligible if self.binary_path else None,
            "validation_receipt_sha256": (
                self.validation_receipt_sha256 if self.binary_path else None
            ),
            "binary_evidence": evidence.to_dict() if evidence else None,
            "license": self.license_name,
            "redistribution_allowed": self.redistribution_allowed,
        }


def select_newest_compatible_runtime(
    candidates: Iterable[RuntimeDependency],
    *,
    target_id: str,
    minimum_version: str,
    schema_version: int,
    requested_game_build: str | None = None,
) -> RuntimeDependency:
    """Select one runtime without allowing a schema/build downgrade."""
    capability = target_capabilities(target_id)
    minimum_key = _version_key(minimum_version)
    eligible = []
    for candidate in candidates:
        candidate.validate()
        if candidate.target_id != capability.target_id:
            continue
        if _version_key(candidate.version) < minimum_key:
            continue
        if candidate.maximum_schema_version < schema_version:
            continue
        if (
            requested_game_build is not None
            and candidate.supported_game_builds
            and requested_game_build not in candidate.supported_game_builds
        ):
            continue
        eligible.append(candidate)
    if not eligible:
        raise ValueError(
            f"No compatible {capability.target_id} axle runtime satisfies schema "
            f"{schema_version} and minimum version {minimum_version}"
        )
    eligible.sort(key=lambda item: _version_key(item.version), reverse=True)
    newest = eligible[0]
    same = [item for item in eligible if item.version == newest.version]
    checksums = {item.checksum() for item in same}
    if len(checksums) > 1:
        raise ValueError("Conflicting runtime binaries share the same name and version")
    return newest


@dataclass(frozen=True)
class StoryRuntimeProfile:
    profile_id: str
    target_id: str
    binary_path: Path
    version: str
    supported_game_builds: tuple[str, ...]
    expected_sha256: str
    package_eligible: bool
    validation_receipt_path: Path
    expected_receipt_sha256: str
    redistribution_allowed: bool = False
    license_name: str = "ALLIN1 Vehicle Workbench Axle Runtime"

    @classmethod
    def from_dict(
        cls, payload: Mapping[str, Any], *, base_directory: Path,
    ) -> "StoryRuntimeProfile":
        builds = payload.get("supported_game_builds")
        if not isinstance(builds, list):
            raise ValueError("Story runtime profile supported_game_builds must be an array")
        _validated_game_builds(builds, "Story runtime profile")

        def local_file(key: str) -> Path:
            value = str(payload.get(key, "")).strip()
            if not value:
                raise ValueError(f"Story runtime profile requires {key}")
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = base_directory / path
            return path.resolve(strict=False)

        return cls(
            profile_id=str(payload.get("profile_id", "")),
            target_id=str(payload.get("target_id", "")).casefold(),
            binary_path=local_file("binary_path"),
            version=str(payload.get("version", "")),
            supported_game_builds=tuple(str(value) for value in builds),
            expected_sha256=str(payload.get("expected_sha256", "")).casefold(),
            package_eligible=_required_json_bool(
                payload, "package_eligible", "Story runtime profile",
            ),
            validation_receipt_path=local_file("validation_receipt_path"),
            expected_receipt_sha256=str(
                payload.get("expected_receipt_sha256", "")
            ).casefold(),
            redistribution_allowed=_required_json_bool(
                payload, "redistribution_allowed", "Story runtime profile",
            ),
            license_name=str(payload.get(
                "license", "ALLIN1 Vehicle Workbench Axle Runtime",
            )),
        )

    @classmethod
    def load(cls, path: Path) -> "StoryRuntimeProfile":
        source = _safe_regular_file(
            path, "Story runtime profile", maximum_bytes=_MAX_STORY_RECEIPT_BYTES,
            suffix=".json",
        )
        return cls.from_dict(
            _bounded_json_object(
                source, "Story runtime profile", _MAX_STORY_RECEIPT_BYTES,
            ),
            base_directory=source.parent,
        )

    def runtime_dependency(self) -> RuntimeDependency:
        capability = target_capabilities(self.target_id)
        if capability.family != "story":
            raise ValueError("Story ASI profile must use a Story Mode target")
        profile_id = self.profile_id.casefold()
        if not _PROFILE_ID.fullmatch(profile_id):
            raise ValueError("Story ASI profile must declare a stable profile id")
        if not self.package_eligible:
            raise ValueError("Story ASI profile is not package eligible")
        builds = _validated_game_builds(
            self.supported_game_builds, "Story ASI profile",
        )
        if not _SHA256.fullmatch(str(self.expected_sha256).casefold()):
            raise ValueError("Story ASI profile must declare a SHA-256 checksum")
        if not _SHA256.fullmatch(str(self.expected_receipt_sha256).casefold()):
            raise ValueError(
                "Story ASI profile must pin its validation receipt SHA-256"
            )
        binary = _safe_regular_file(
            self.binary_path, "Story runtime binary",
            maximum_bytes=_MAX_STORY_RUNTIME_BYTES, suffix=".asi",
        )
        evidence = inspect_story_runtime_binary(binary)
        return RuntimeDependency(
            name=STORY_RUNTIME_NAME.casefold(),
            version=_validated_version(self.version, "Story runtime version"),
            maximum_schema_version=AXLE_SCHEMA_VERSION,
            target_id=capability.target_id,
            supported_game_builds=builds,
            configuration_destination=f"{STORY_RUNTIME_NAME}/configs",
            binary_path=binary,
            binary_sha256=str(self.expected_sha256).casefold(),
            profile_id=profile_id,
            package_eligible=True,
            validation_receipt_path=Path(self.validation_receipt_path),
            validation_receipt_sha256=str(self.expected_receipt_sha256).casefold(),
            binary_evidence=evidence,
            license_name=self.license_name,
            redistribution_allowed=self.redistribution_allowed,
        ).validate()

    def verification_report(self) -> dict[str, Any]:
        report = {
            "profile_id": self.profile_id,
            "target_id": self.target_id,
            "version": self.version,
            "supported_game_builds": list(self.supported_game_builds),
            "binary_file": Path(self.binary_path).name,
            "receipt_file": Path(self.validation_receipt_path).name,
            "package_eligible": False,
            "verified": False,
            "reason": None,
        }
        try:
            dependency = self.runtime_dependency()
        except (OSError, TypeError, ValueError) as exc:
            report["reason"] = str(exc)
            return report
        report.update({
            "package_eligible": True,
            "verified": True,
            "reason": None,
            "binary_sha256": dependency.checksum(),
            "validation_receipt_sha256": dependency.validation_receipt_sha256,
            "binary_evidence": dependency.binary_evidence.to_dict()
            if dependency.binary_evidence else None,
        })
        return report


def story_runtime_profile_report(
    profiles: Iterable[StoryRuntimeProfile] = (),
    *,
    requested_game_builds: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return the explicit Story profile and target/build trust mapping.

    No repository-local or user-local profile is implicitly trusted. Callers
    must pass each profile, keeping the default catalog empty and fail closed.
    """
    by_target: dict[str, StoryRuntimeProfile] = {}
    for profile in profiles:
        target = str(profile.target_id).strip().casefold()
        if target not in {TARGET_STORY_LEGACY, TARGET_STORY_ENHANCED}:
            raise ValueError("Story runtime profile catalog accepts Story targets only")
        if target in by_target:
            raise ValueError(f"Duplicate Story runtime profile for {target}")
        by_target[target] = profile
    builds = {
        str(target).strip().casefold(): str(build).strip()
        for target, build in dict(requested_game_builds or {}).items()
    }
    unknown = sorted(set(builds) - {TARGET_STORY_LEGACY, TARGET_STORY_ENHANCED})
    if unknown:
        raise ValueError(
            "Story target/build mapping contains non-Story targets: "
            + ", ".join(unknown)
        )
    if any(not build for build in builds.values()):
        raise ValueError("Story target/build mapping cannot contain an empty build")

    targets: dict[str, Any] = {}
    for target in (TARGET_STORY_LEGACY, TARGET_STORY_ENHANCED):
        requested = builds.get(target)
        profile = by_target.get(target)
        if profile is None:
            targets[target] = {
                "requested_game_build": requested,
                "profile": None,
                "build_mapped": False,
                "package_eligible_for_build": False,
                "reason": "No explicit Story runtime profile was supplied",
            }
            continue
        verification = profile.verification_report()
        mapped = requested is None or requested in profile.supported_game_builds
        reason = verification.get("reason")
        if verification["verified"] and not mapped:
            reason = f"Profile does not support requested game build {requested}"
        targets[target] = {
            "requested_game_build": requested,
            "profile": verification,
            "build_mapped": mapped,
            "package_eligible_for_build": bool(verification["verified"] and mapped),
            "reason": reason,
        }
    return {
        "schema_version": 1,
        "implicit_profiles_loaded": False,
        "required_exports": list(STORY_RUNTIME_REQUIRED_EXPORTS),
        "targets": targets,
    }


@dataclass(frozen=True)
class ExternalToolApproval:
    tool_id: str
    executable: Path
    approved: bool
    source_url: str
    license_name: str
    redistribution_allowed: bool = False

    def validate(self) -> "ExternalToolApproval":
        _safe_id(self.tool_id, "Converter id")
        path = Path(self.executable).expanduser().resolve(strict=False)
        if not self.approved:
            raise ValueError("Enhanced asset converter invocation was not explicitly approved")
        if not path.is_file() or path.is_symlink():
            raise ValueError("Enhanced asset converter executable is missing or unsafe")
        if not self.source_url.startswith(("https://", "http://")):
            raise ValueError("Enhanced asset converter must provide an official source URL")
        if not self.license_name.strip():
            raise ValueError("Enhanced asset converter must declare licensing terms")
        return self


class EnhancedAssetConverter(Protocol):
    """Caller-owned adapter; the SDK never guesses an external CLI contract."""

    converter_id: str
    supported_targets: frozenset[str]

    def convert(
        self,
        source: Path,
        destination: Path,
        *,
        target_id: str,
        tool: ExternalToolApproval,
    ) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class VehicleAxleBuildInput:
    configuration: AxleConfiguration
    configuration_id: str
    model_hash: int | str
    minimum_runtime_version: str = DEFAULT_RUNTIME_VERSION
    exported_wheel_indices: Mapping[str, int] | None = None
    reported_wheel_count: int | None = None
    asset_source: Path | None = None
    dual_tyre_geometry: tuple[str, ...] = ()

    @property
    def normalized_configuration_id(self) -> str:
        return _safe_id(self.configuration_id, "Configuration id")

    @property
    def normalized_model_hash(self) -> str:
        return _model_hash(self.model_hash)


@dataclass(frozen=True)
class ResolvedWheelMap:
    source: str
    by_bone: Mapping[str, int]
    reported_wheel_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "reported_wheel_count": self.reported_wheel_count,
            "by_bone": dict(sorted(self.by_bone.items(), key=lambda item: item[1])),
        }


def resolve_runtime_wheel_map(
    vehicle: VehicleAxleBuildInput,
    target_id: str,
) -> ResolvedWheelMap:
    """Resolve target indices from canonical semantics or explicit export data.

    This intentionally does not use physical axle order as an arithmetic index.
    """
    capability = target_capabilities(target_id)
    configured_bones = tuple(
        bone
        for axle in vehicle.configuration.axles
        for bone in (axle.left_bone.casefold(), axle.right_bone.casefold())
    )
    if len(vehicle.configuration.axles) not in range(
        capability.minimum_physical_axles, capability.maximum_physical_axles + 1,
    ):
        raise ValueError(
            f"{capability.target_id} recognizes {capability.minimum_physical_axles}-"
            f"{capability.maximum_physical_axles} canonical physical axle pairs; "
            "additional wheels require cosmetic geometry or a future custom-physics extension"
        )
    if len(configured_bones) != len(set(configured_bones)):
        raise ValueError("A wheel bone is assigned to more than one physical axle")
    canonical = set(_CANONICAL_WHEEL_SEQUENCE)
    unknown = sorted(set(configured_bones) - canonical)
    if unknown:
        raise ValueError("Runtime wheel mapping contains non-canonical bones: " + ", ".join(unknown))

    if vehicle.exported_wheel_indices is not None:
        exported: dict[str, int] = {}
        for raw_bone, raw_index in vehicle.exported_wheel_indices.items():
            bone = str(raw_bone).strip().casefold()
            if bone not in configured_bones:
                continue
            if isinstance(raw_index, bool) or not isinstance(raw_index, int) or raw_index < 0:
                raise ValueError(f"Exported runtime index for {bone} is invalid")
            exported[bone] = raw_index
        missing = sorted(set(configured_bones) - set(exported))
        if missing:
            raise ValueError("Exported vehicle information omitted wheel bones: " + ", ".join(missing))
        mapping = exported
        source = "exported_vehicle_information"
    else:
        # Target rule is a canonical-name sequence. Filtering it to present bones
        # handles front/rear, front/middle/rear, and all five canonical pairs
        # without deriving an index from the axle's display order.
        semantic_sequence = tuple(
            bone for bone in _CANONICAL_WHEEL_SEQUENCE if bone in configured_bones
        )
        mapping = {bone: index for index, bone in enumerate(semantic_sequence)}
        source = f"{capability.target_id}_canonical_semantics_v1"

    indices = tuple(mapping[bone] for bone in configured_bones)
    if len(set(indices)) != len(indices):
        raise ValueError("Runtime wheel-index map contains duplicate indices")
    reported = (
        vehicle.reported_wheel_count
        if vehicle.reported_wheel_count is not None else len(mapping)
    )
    if isinstance(reported, bool) or not isinstance(reported, int) or reported < 1:
        raise ValueError("Game-reported wheel count must be a positive integer")
    if reported != len(mapping):
        raise ValueError(
            f"Runtime wheel-index map has {len(mapping)} wheels but the game reports {reported}"
        )
    if any(index >= reported for index in indices):
        raise ValueError("Runtime wheel-index map contains an out-of-range index")
    return ResolvedWheelMap(source, mapping, reported)


def _runtime_configuration(
    vehicle: VehicleAxleBuildInput,
    target_id: str,
) -> tuple[AxleConfiguration, ResolvedWheelMap]:
    wheel_map = resolve_runtime_wheel_map(vehicle, target_id)
    axles = tuple(
        replace(
            axle,
            left_runtime_index=wheel_map.by_bone[axle.left_bone.casefold()],
            right_runtime_index=wheel_map.by_bone[axle.right_bone.casefold()],
        )
        for axle in vehicle.configuration.axles
    )
    return replace(
        vehicle.configuration,
        export_mode=EXPORT_FIVEM_RUNTIME,
        axles=axles,
        configuration_id=vehicle.normalized_configuration_id,
        model_hash=vehicle.normalized_model_hash,
        minimum_runtime_version=_validated_version(vehicle.minimum_runtime_version),
    ), wheel_map


def _symbolic_handling(config: AxleConfiguration) -> dict[str, Any]:
    ordered = sorted(config.axles, key=lambda axle: axle.physical_order)
    steered = [axle for axle in ordered if axle.steered]
    set_flags: list[str] = []
    clear_flags = ["HF_STEER_REARWHEELS", "HF_HANDBRAKE_REARWHEELSTEER"]
    if len(steered) == len(ordered) or (
        len(ordered) >= 3
        and ordered[0].steered and ordered[-1].steered
        and any(not axle.steered for axle in ordered[1:-1])
    ):
        mode = "all_wheels"
        set_flags.append("HF_STEER_ALL_WHEELS")
    elif steered and steered == [ordered[-1]]:
        mode = "rear_only"
        set_flags.append("HF_STEER_REARWHEELS")
        clear_flags = ["HF_STEER_ALL_WHEELS", "HF_HANDBRAKE_REARWHEELSTEER"]
    elif steered and steered == [ordered[0]]:
        mode = "front_only"
        clear_flags.append("HF_STEER_ALL_WHEELS")
    else:
        mode = "custom_runtime"
        set_flags.append("HF_STEER_ALL_WHEELS")
    powered = [axle for axle in ordered if axle.powered]
    return {
        "baseSteeringMode": mode,
        "setHandlingFlags": set_flags,
        "clearHandlingFlags": list(dict.fromkeys(clear_flags)),
        "driveBiasRequirement": (
            "Base handling must route torque to every selected powered axle; "
            "use an AWD-compatible fDriveBiasFront for selective middle/rear drive."
            if powered and len(powered) != len(ordered) else None
        ),
    }


def compatibility_configuration(
    vehicle: VehicleAxleBuildInput,
    target_id: str,
) -> dict[str, Any]:
    runtime_config, wheel_map = _runtime_configuration(vehicle, target_id)
    ordered = sorted(runtime_config.axles, key=lambda axle: axle.physical_order)
    return {
        "schemaVersion": AXLE_SCHEMA_VERSION,
        "configurationId": vehicle.normalized_configuration_id,
        "modelName": runtime_config.vehicle_model,
        "modelHash": vehicle.normalized_model_hash,
        "expectedWheelCount": wheel_map.reported_wheel_count,
        "minimumRuntimeVersion": _validated_version(vehicle.minimum_runtime_version),
        "handling": _symbolic_handling(runtime_config),
        "axles": [
            {
                "order": index,
                "role": axle.logical_role,
                "leftBone": axle.left_bone,
                "rightBone": axle.right_bone,
                "wheelIndices": [
                    wheel_map.by_bone[axle.left_bone.casefold()],
                    wheel_map.by_bone[axle.right_bone.casefold()],
                ],
                "steered": axle.steered,
                "powered": axle.powered,
                "serviceBrake": axle.service_brake,
                "handbrake": axle.handbrake,
                "visualFamily": axle.visual_family,
            }
            for index, axle in enumerate(ordered)
        ],
        "dualTyreGeometry": list(vehicle.dual_tyre_geometry),
        "dualTyresConsumePhysicalSlots": False,
        "wheelIndexMapping": wheel_map.to_dict(),
        "compatibility": {target: target == target_id for target in TARGET_IDS},
    }


@dataclass(frozen=True)
class PlannedConfiguration:
    source: VehicleAxleBuildInput
    runtime_configuration: AxleConfiguration
    runtime_payload: Mapping[str, Any]
    wheel_map: ResolvedWheelMap


@dataclass(frozen=True)
class TargetBuildPlan:
    target_id: str
    capabilities: TargetCapabilities
    status: str
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    configurations: tuple[PlannedConfiguration, ...]
    runtime: RuntimeDependency | None
    dependencies: tuple[DependencyDeclaration, ...]
    asset_mode: str
    converter: ExternalToolApproval | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "status": self.status,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "capabilities": self.capabilities.to_dict(),
            "runtime": self.runtime.to_dict() if self.runtime is not None else None,
            "dependencies": [item.to_dict() for item in self.dependencies],
            "asset_mode": self.asset_mode,
            "configurations": [dict(item.runtime_payload) for item in self.configurations],
        }


@dataclass(frozen=True)
class AxleBundlePlan:
    schema_version: int
    targets: tuple[TargetBuildPlan, ...]
    requested_targets: tuple[str, ...]
    duplicate_model_hashes_checked: bool = True
    direct_install: bool = False

    @property
    def ready_targets(self) -> tuple[TargetBuildPlan, ...]:
        return tuple(item for item in self.targets if item.status == STATUS_READY)

    @property
    def omitted_targets(self) -> tuple[TargetBuildPlan, ...]:
        return tuple(item for item in self.targets if item.status == STATUS_OMITTED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "requested_targets": list(self.requested_targets),
            "direct_install": self.direct_install,
            "duplicate_model_hashes_checked": self.duplicate_model_hashes_checked,
            "targets": [target.to_dict() for target in self.targets],
        }


class AxleRuntimeBundlePlanner:
    def plan(
        self,
        vehicles: Sequence[VehicleAxleBuildInput],
        *,
        targets: Sequence[str] = TARGET_IDS,
        story_profiles: Mapping[str, StoryRuntimeProfile] | None = None,
        requested_game_builds: Mapping[str, str] | None = None,
        converter: EnhancedAssetConverter | None = None,
        converter_approval: ExternalToolApproval | None = None,
        direct_install: bool = False,
    ) -> AxleBundlePlan:
        if direct_install:
            raise ValueError(
                "Direct GTA installation is not supported by the axle bundler; create a staged package"
            )
        if not vehicles:
            raise ValueError("At least one axle configuration is required")
        normalized_targets = tuple(dict.fromkeys(str(item).strip().casefold() for item in targets))
        if not normalized_targets:
            raise ValueError("At least one axle bundle target is required")
        for target in normalized_targets:
            target_capabilities(target)
        self._validate_vehicle_identity(vehicles)
        profiles: dict[str, StoryRuntimeProfile] = {}
        for raw_target, profile in dict(story_profiles or {}).items():
            profile_target = str(raw_target).strip().casefold()
            if target_capabilities(profile_target).family != "story":
                raise ValueError("Story runtime profiles can map to Story targets only")
            if profile_target in profiles:
                raise ValueError(f"Duplicate Story runtime profile for {profile_target}")
            profiles[profile_target] = profile
        builds: dict[str, str] = {}
        for raw_target, raw_build in dict(requested_game_builds or {}).items():
            build_target = str(raw_target).strip().casefold()
            target_capabilities(build_target)
            build = str(raw_build).strip()
            if not build or len(build) > 96 or any(
                character in build for character in "\r\n"
            ):
                raise ValueError(f"Invalid requested game build for {build_target}")
            builds[build_target] = build
        plans = tuple(
            self._target_plan(
                target_capabilities(target), vehicles,
                profile=profiles.get(target),
                requested_game_build=builds.get(target),
                converter=converter,
                approval=converter_approval,
            )
            for target in normalized_targets
        )
        return AxleBundlePlan(
            BUNDLE_SCHEMA_VERSION, plans, normalized_targets,
            duplicate_model_hashes_checked=True, direct_install=False,
        )

    @staticmethod
    def _validate_vehicle_identity(vehicles: Sequence[VehicleAxleBuildInput]) -> None:
        hashes: dict[str, str] = {}
        models: set[str] = set()
        ids: set[str] = set()
        for vehicle in vehicles:
            digest = vehicle.normalized_model_hash
            model = vehicle.configuration.vehicle_model.casefold()
            config_id = vehicle.normalized_configuration_id
            if digest in hashes:
                raise ValueError(
                    f"Duplicate model hash {digest} is assigned to {hashes[digest]} and {model}"
                )
            if model in models:
                raise ValueError(f"Duplicate vehicle model configuration: {model}")
            if config_id in ids:
                raise ValueError(f"Duplicate axle configuration id: {config_id}")
            hashes[digest] = model
            models.add(model)
            ids.add(config_id)
        for vehicle in vehicles:
            digest = vehicle.normalized_model_hash
            model = vehicle.configuration.vehicle_model.casefold()
            config_id = vehicle.normalized_configuration_id
            expected_hash = joaat_hex(model)
            authored = str(vehicle.configuration.model_hash or "").strip()
            authored_hash = _model_hash(authored) if authored else ""
            if digest != expected_hash:
                raise ValueError(
                    f"Model hash {digest} does not match {model}'s GTA joaat hash {expected_hash}"
                )
            if authored_hash and authored_hash != digest:
                raise ValueError(
                    f"Build model hash does not match {model}'s axle configuration"
                )
            authored_id = str(vehicle.configuration.configuration_id or "").strip().casefold()
            if authored_id and authored_id != config_id:
                raise ValueError(
                    f"Build configuration id does not match {model}'s axle configuration"
                )

    def _target_plan(
        self,
        capability: TargetCapabilities,
        vehicles: Sequence[VehicleAxleBuildInput],
        *,
        profile: StoryRuntimeProfile | None,
        requested_game_build: str | None,
        converter: EnhancedAssetConverter | None,
        approval: ExternalToolApproval | None,
    ) -> TargetBuildPlan:
        reasons: list[str] = []
        warnings: list[str] = []
        dependencies: list[DependencyDeclaration] = []
        runtime: RuntimeDependency | None = None

        if not capability.supports_current_axle_schema \
                or AXLE_SCHEMA_VERSION > capability.maximum_axle_schema:
            reasons.append("Target runtime does not support the current axle configuration schema")
        for vehicle in vehicles:
            declared = dict(vehicle.configuration.compatibility)
            if declared and not declared.get(capability.target_id, False):
                reasons.append(
                    f"{vehicle.configuration.vehicle_model}: shared axle configuration "
                    f"does not enable {capability.target_id}"
                )
        if requested_game_build is not None and capability.supported_game_builds \
                and requested_game_build not in capability.supported_game_builds:
            reasons.append(f"Unsupported target game build: {requested_game_build}")

        if capability.family == "story":
            dependencies.append(
                SCRIPHOOK_ENHANCED_DEPENDENCY
                if capability.edition == "enhanced" else SCRIPHOOK_DEPENDENCY
            )
            if profile is None:
                reasons.append("Missing ASI build profile; no Story runtime binary was fabricated")
            else:
                try:
                    runtime = profile.runtime_dependency()
                    if profile.target_id != capability.target_id:
                        raise ValueError("ASI build profile targets a different edition")
                    if requested_game_build is not None \
                            and requested_game_build not in runtime.supported_game_builds:
                        raise ValueError(
                            f"ASI build profile does not support game build {requested_game_build}"
                        )
                except ValueError as exc:
                    reasons.append(str(exc))
        else:
            runtime = RuntimeDependency(
                name=FIVEM_RUNTIME_NAME,
                version=capability.runtime_implementation_version,
                maximum_schema_version=capability.maximum_axle_schema,
                target_id=capability.target_id,
                supported_game_builds=capability.supported_game_builds,
                configuration_destination="axle-runtime/configs",
            ).validate()

        has_assets = any(vehicle.asset_source is not None for vehicle in vehicles)
        asset_mode = "none"
        selected_approval = None
        if has_assets and capability.edition == "legacy":
            asset_mode = "copy_validated_source"
        elif has_assets and capability.target_id == TARGET_FIVEM_ENHANCED:
            dependencies.append(ALCHEMIST_DEPENDENCY)
            if converter is None or approval is None:
                reasons.append("Missing approved Enhanced asset converter")
            else:
                try:
                    approval.validate()
                    if capability.target_id not in converter.supported_targets:
                        raise ValueError("Configured converter does not support fivem-enhanced")
                    if converter.converter_id.casefold() != approval.tool_id.casefold():
                        raise ValueError("Converter adapter and approved tool id do not match")
                    selected_approval = approval
                    asset_mode = "approved_external_conversion"
                except ValueError as exc:
                    reasons.append(str(exc))
        elif has_assets and capability.target_id == TARGET_STORY_ENHANCED:
            # A FiveM converter is not presumed valid for Story Enhanced.
            if (
                converter is not None and approval is not None
                and capability.target_id in converter.supported_targets
            ):
                try:
                    approval.validate()
                    if converter.converter_id.casefold() != approval.tool_id.casefold():
                        raise ValueError("Converter adapter and approved tool id do not match")
                    selected_approval = approval
                    asset_mode = "approved_external_conversion"
                except ValueError as exc:
                    warnings.append(str(exc))
                    asset_mode = "manual_asset_installation_required"
            else:
                asset_mode = "manual_asset_installation_required"
                warnings.append(
                    "Story Enhanced asset conversion is not configured; runtime and configs "
                    "can be staged, but vehicle assets require separate installation instructions"
                )

        configurations: list[PlannedConfiguration] = []
        if not reasons:
            for vehicle in vehicles:
                try:
                    _validated_version(vehicle.minimum_runtime_version)
                    if runtime is None or _version_key(runtime.version) < _version_key(
                        vehicle.minimum_runtime_version
                    ):
                        raise ValueError(
                            f"Runtime {runtime.version if runtime else 'missing'} is older than "
                            f"{vehicle.configuration.vehicle_model}'s minimum runtime version"
                        )
                    if runtime.maximum_schema_version < vehicle.configuration.schema_version:
                        raise ValueError("Configuration/runtime schema mismatch")
                    # Shared validation catches physical-role/bone issues. Runtime
                    # index findings are intentionally superseded by target mapping.
                    findings = validate_axle_configuration(vehicle.configuration)
                    fatal = [
                        item for item in findings
                        if item.severity == "error"
                        and item.code not in {"runtime_indices", "wheel_index_count"}
                    ]
                    if fatal:
                        raise ValueError(fatal[0].message)
                    runtime_config, wheel_map = _runtime_configuration(
                        vehicle, capability.target_id,
                    )
                    payload = compatibility_configuration(vehicle, capability.target_id)
                    configurations.append(PlannedConfiguration(
                        vehicle, runtime_config, payload, wheel_map,
                    ))
                except ValueError as exc:
                    reasons.append(
                        f"{vehicle.configuration.vehicle_model}: {exc}"
                    )
        status = STATUS_OMITTED if reasons else STATUS_READY
        return TargetBuildPlan(
            target_id=capability.target_id,
            capabilities=capability,
            status=status,
            reasons=tuple(dict.fromkeys(reasons)),
            warnings=tuple(dict.fromkeys(warnings)),
            configurations=tuple(configurations) if status == STATUS_READY else (),
            runtime=runtime if status == STATUS_READY else None,
            dependencies=tuple(dependencies),
            asset_mode=asset_mode,
            converter=selected_approval,
        )


@dataclass(frozen=True)
class AxleBundleResult:
    root: Path
    manifest: Path
    built_targets: tuple[str, ...]
    omitted_targets: Mapping[str, tuple[str, ...]]
    files: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "root": str(self.root),
            "manifest": str(self.manifest),
            "built_targets": list(self.built_targets),
            "omitted_targets": {key: list(value) for key, value in self.omitted_targets.items()},
            "files": list(self.files),
            "game_write_performed": False,
        }


class AxleRuntimeBundleBuilder:
    """Atomically publish one or more capability-qualified target packages."""

    def build(
        self,
        plan: AxleBundlePlan,
        destination: str | Path,
        *,
        converter: EnhancedAssetConverter | None = None,
    ) -> AxleBundleResult:
        if plan.direct_install:
            raise ValueError("Direct installation is forbidden for staged axle bundles")
        target = Path(destination).expanduser().resolve(strict=False)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Axle bundle destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(
            prefix=f".{target.name}.axle-bundle-", dir=target.parent,
        )).resolve()
        try:
            for target_plan in plan.ready_targets:
                output = stage / target_plan.target_id
                output.mkdir()
                if target_plan.capabilities.family == "fivem":
                    self._write_fivem_target(target_plan, output)
                else:
                    self._write_story_target(target_plan, output)
                self._write_assets(target_plan, output, converter)
                self._write_target_manifest(target_plan, output)
                self._validate_target_contamination(target_plan, output)
            self._write_bundle_manifest(plan, stage)
            stage.replace(target)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        files = tuple(sorted(
            path.relative_to(target).as_posix()
            for path in target.rglob("*") if path.is_file()
        ))
        return AxleBundleResult(
            root=target,
            manifest=target / "bundle-manifest.json",
            built_targets=tuple(item.target_id for item in plan.ready_targets),
            omitted_targets={item.target_id: item.reasons for item in plan.omitted_targets},
            files=files,
        )

    @staticmethod
    def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _write_fivem_target(self, plan: TargetBuildPlan, output: Path) -> None:
        runtime_root = output / "axle-runtime"
        configs = runtime_root / "configs"
        models = runtime_root / "models"
        configs.mkdir(parents=True)
        models.mkdir()
        scripts = []
        server_chunks = []
        for planned in plan.configurations:
            slug = planned.source.normalized_configuration_id
            self._write_json(configs / f"{slug}.json", planned.runtime_payload)
            # Reuse the shared, tested event-driven runtime generator. Multiple
            # vehicle wrappers live in one resource, so the runtime dependency is
            # still installed/deduplicated once per target.
            script = f"models/{slug}.lua"
            (runtime_root / script).write_text(
                self._fivem_wrapper_lua(planned.runtime_configuration), encoding="utf-8",
            )
            scripts.append(script)
            server_chunks.append(
                "do\n" + fivem_server_lua(planned.runtime_configuration) + "end\n"
            )
        script_rows = "\n".join(f"    '{value}'," for value in scripts)
        (runtime_root / "fxmanifest.lua").write_text(
            "fx_version 'cerulean'\n"
            "game 'gta5'\n"
            f"name '{FIVEM_RUNTIME_NAME}'\n"
            f"version '{plan.runtime.version if plan.runtime else DEFAULT_RUNTIME_VERSION}'\n"
            "client_scripts {\n"
            f"{script_rows}\n"
            "}\n"
            "server_script 'server.lua'\n"
            "files { 'configs/*.json' }\n",
            encoding="utf-8",
        )
        (runtime_root / "server.lua").write_text(
            "-- Shared-generator server creation hints, scoped per configured model.\n"
            + "\n".join(server_chunks),
            encoding="utf-8",
        )

    @staticmethod
    def _fivem_wrapper_lua(config: AxleConfiguration) -> str:
        """Harden the shared generator for current CitizenFX event/flag rules.

        The configurator remains the single source for behavior. This wrapper
        removes its legacy client-only creation hook, adds the server-signalled
        owner-client path, requires entity control, and bounds the uint16 flag
        field after read-modify-write.
        """
        script = fivem_client_lua(config)
        if (
            "NetworkHasControlOfEntity(vehicle)" in script
            and "0xFFFF" in script
            and 'AddEventHandler("entityCreated"' not in script
            and 'RegisterNetEvent("allin1_axles:created"' in script
        ):
            return script
        owner_old = (
            "return not NetworkGetEntityIsNetworked(vehicle)\n"
            "        or NetworkGetEntityOwner(vehicle) == PlayerId()"
        )
        owner_new = (
            "return not NetworkGetEntityIsNetworked(vehicle)\n"
            "        or (NetworkGetEntityOwner(vehicle) == PlayerId()\n"
            "            and NetworkHasControlOfEntity(vehicle))"
        )
        flag_old = (
            "if desired.steered then flags = flags | STEER else flags = flags & ~STEER end"
        )
        flag_new = (
            "if desired.steered then flags = (flags | STEER) & 0xFFFF "
            "else flags = (flags & ~STEER) & 0xFFFF end"
        )
        if owner_old not in script or flag_old not in script:
            raise RuntimeError("Shared FiveM axle generator contract changed unexpectedly")
        script = script.replace(owner_old, owner_new).replace(flag_old, flag_new)
        creation = (
            'AddEventHandler("entityCreated", function(entity) SetTimeout(0, '
            'function() applyAxles(entity, "created") end) end)'
        )
        script = script.replace(
            creation,
            "-- entityCreated is server-side; the owning client receives a bounded signal.",
        )
        if config.runtime_reapplication.on_entity_created:
            marker = "local function applyExisting(reason)"
            listener = (
                "RegisterNetEvent('allin1-axles:entity-created', function(networkId)\n"
                "    local vehicle = NetworkGetEntityFromNetworkId(networkId)\n"
                "    if vehicle and vehicle ~= 0 then\n"
                "        SetTimeout(0, function() applyAxles(vehicle, 'created') end)\n"
                "    end\n"
                "end)\n\n"
            )
            if marker not in script:
                raise RuntimeError("Shared FiveM axle generator lacks its applyExisting boundary")
            script = script.replace(marker, listener + marker, 1)
        return script

    def _write_story_target(self, plan: TargetBuildPlan, output: Path) -> None:
        if plan.runtime is None or plan.runtime.binary_path is None:
            raise ValueError("Story target reached build without a validated ASI profile")
        binary = Path(plan.runtime.binary_path).resolve()
        if _sha256_file(binary) != plan.runtime.checksum():
            raise ValueError("Story runtime binary changed after planning")
        if plan.runtime.validation_receipt_path is None:
            raise ValueError("Story runtime validation receipt is missing after planning")
        receipt = Path(plan.runtime.validation_receipt_path).resolve()
        if _sha256_file(receipt) != plan.runtime.validation_receipt_sha256:
            raise ValueError("Story runtime validation receipt changed after planning")
        plan.runtime.validate()
        shutil.copyfile(binary, output / f"{STORY_RUNTIME_NAME}.asi")
        configs = output / STORY_RUNTIME_NAME / "configs"
        configs.mkdir(parents=True)
        shutil.copyfile(
            receipt, output / STORY_RUNTIME_NAME / "validation-receipt.json",
        )
        for planned in plan.configurations:
            self._write_json(
                configs / f"{planned.source.normalized_configuration_id}.json",
                planned.runtime_payload,
            )
        instructions = [
            f"# {plan.target_id} axle runtime",
            "",
            "This is a staged Story Mode package. It does not install itself.",
            "Install only in GTA V Story Mode with the correct ScriptHookV edition.",
            "The runtime must disable itself in online/network sessions.",
            "Do not replace a newer installed runtime with this package.",
        ]
        if plan.asset_mode == "manual_asset_installation_required":
            instructions.extend((
                "",
                "Vehicle assets were not converted or installed. Configure a supported",
                "Story Enhanced asset pipeline separately; FiveM Alchemist output is not",
                "assumed to be a valid Story Mode installation.",
            ))
        (output / "README.md").write_text("\n".join(instructions) + "\n", encoding="utf-8")

    def _write_assets(
        self,
        plan: TargetBuildPlan,
        output: Path,
        converter: EnhancedAssetConverter | None,
    ) -> None:
        sources = [
            (item.source, Path(item.source.asset_source).expanduser().resolve(strict=False))
            for item in plan.configurations if item.source.asset_source is not None
        ]
        if not sources or plan.asset_mode in {"none", "manual_asset_installation_required"}:
            return
        if plan.asset_mode == "copy_validated_source":
            asset_root = output / "vehicle-resource"
            asset_root.mkdir()
            for vehicle, source in sources:
                self._copy_asset_source(source, asset_root / vehicle.normalized_configuration_id)
            return
        if plan.asset_mode != "approved_external_conversion" \
                or converter is None or plan.converter is None:
            raise ValueError("Enhanced conversion plan is missing its approved adapter")
        if converter.converter_id.casefold() != plan.converter.tool_id.casefold():
            raise ValueError("Build converter does not match the approved planning adapter")
        asset_root = output / (
            "vehicle-resource-gen9"
            if plan.capabilities.family == "fivem" else "vehicle-assets-enhanced"
        )
        asset_root.mkdir()
        for vehicle, source in sources:
            self._validate_asset_source(source)
            destination = asset_root / vehicle.normalized_configuration_id
            destination.mkdir()
            evidence = converter.convert(
                source, destination, target_id=plan.target_id, tool=plan.converter,
            )
            if not isinstance(evidence, Mapping):
                raise ValueError("Enhanced converter did not return structured evidence")
            self._write_json(destination / "conversion-evidence.json", dict(evidence))

    @staticmethod
    def _validate_asset_source(source: Path) -> None:
        if not source.exists() or source.is_symlink():
            raise ValueError(f"Vehicle asset source is missing or unsafe: {source.name}")
        if source.is_dir():
            for member in source.rglob("*"):
                if member.is_symlink():
                    raise ValueError("Vehicle asset source contains a symbolic link")
        elif not source.is_file():
            raise ValueError("Vehicle asset source is not a regular file or directory")

    def _copy_asset_source(self, source: Path, destination: Path) -> None:
        self._validate_asset_source(source)
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            destination.mkdir()
            shutil.copyfile(source, destination / source.name)

    def _write_target_manifest(self, plan: TargetBuildPlan, output: Path) -> None:
        files = []
        for path in sorted(output.rglob("*")):
            if path.is_file():
                files.append({
                    "path": path.relative_to(output).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha256_file(path),
                })
        payload = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "target": plan.target_id,
            "edition": plan.capabilities.edition,
            "runtime_family": plan.capabilities.family,
            "acceptance_status": plan.capabilities.acceptance_status,
            "published_supported": False,
            "runtime": plan.runtime.to_dict() if plan.runtime else None,
            "dependencies": [item.to_dict() for item in plan.dependencies],
            "asset_packaging": {
                "mode": plan.asset_mode,
                "warnings": list(plan.warnings),
            },
            "configurations": [
                {
                    "configuration_id": item.source.normalized_configuration_id,
                    "model_name": item.source.configuration.vehicle_model,
                    "model_hash": item.source.normalized_model_hash,
                    "schema_version": item.source.configuration.schema_version,
                    "minimum_runtime_version": item.source.minimum_runtime_version,
                    "wheel_mapping": item.wheel_map.to_dict(),
                }
                for item in plan.configurations
            ],
            "supported_game_builds": (
                list(plan.runtime.supported_game_builds) if plan.runtime else []
            ),
            "safety": {
                "staged_only": True,
                "game_write_performed": False,
                "online_loading_supported": False,
                "unknown_wheel_bits_preserved_by_runtime": True,
                "fixed_memory_offsets_embedded_by_bundler": False,
            },
            "files": files,
        }
        self._write_json(output / "compatibility-manifest.json", payload)

    @staticmethod
    def _validate_target_contamination(plan: TargetBuildPlan, output: Path) -> None:
        files = [path for path in output.rglob("*") if path.is_file()]
        names = {path.name.casefold() for path in files}
        forbidden_dependencies = names & _THIRD_PARTY_BINARY_NAMES
        if forbidden_dependencies:
            raise ValueError(
                "Target contains an unapproved third-party dependency binary: "
                + ", ".join(sorted(forbidden_dependencies))
            )
        if plan.capabilities.family == "fivem":
            if any(path.suffix.casefold() == ".asi" for path in files):
                raise ValueError("FiveM target is contaminated with a Story Mode ASI")
            if "fxmanifest.lua" not in names:
                raise ValueError("FiveM target is missing its resource manifest")
        else:
            if any(path.name.casefold() == "fxmanifest.lua" for path in files) \
                    or any(path.suffix.casefold() == ".lua" for path in files):
                raise ValueError("Story target is contaminated with a FiveM resource")
            expected = f"{STORY_RUNTIME_NAME}.asi".casefold()
            if expected not in names:
                raise ValueError("Story target is missing its validated ASI runtime")

    def _write_bundle_manifest(self, plan: AxleBundlePlan, stage: Path) -> None:
        built = {}
        for target in plan.ready_targets:
            manifest = stage / target.target_id / "compatibility-manifest.json"
            built[target.target_id] = {
                "path": f"{target.target_id}/compatibility-manifest.json",
                "sha256": _sha256_file(manifest),
            }
        payload = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "operation": "build_axle_runtime_bundle",
            "staged_only": True,
            "game_write_performed": False,
            "acceptance_status": ACCEPTANCE_PENDING,
            "built_targets": built,
            "omitted_targets": {
                target.target_id: list(target.reasons) for target in plan.omitted_targets
            },
            "validation": {
                "duplicate_model_hashes": "passed",
                "cross_target_runtime_contamination": "passed",
                "dependency_redistribution": "passed",
                "direct_install": "disabled",
            },
        }
        self._write_json(stage / "bundle-manifest.json", payload)


__all__ = [
    "ACCEPTANCE_PENDING", "ALCHEMIST_DEPENDENCY", "BUNDLE_SCHEMA_VERSION",
    "DEFAULT_RUNTIME_VERSION", "FIVEM_RUNTIME_NAME", "RUNTIME_CONTRACT_VERSION",
    "STATUS_OMITTED", "STATUS_READY", "STORY_RUNTIME_NAME",
    "STORY_RUNTIME_DESCRIPTOR_EXPORT", "STORY_RUNTIME_PROFILE_EXPORT",
    "STORY_RUNTIME_REQUIRED_EXPORTS", "STORY_RUNTIME_RECEIPT_SCHEMA_VERSION",
    "TARGET_CAPABILITIES", "TARGET_FIVEM_ENHANCED", "TARGET_FIVEM_LEGACY",
    "TARGET_IDS", "TARGET_STORY_ENHANCED", "TARGET_STORY_LEGACY",
    "AxleBundlePlan", "AxleBundleResult", "AxleRuntimeBundleBuilder",
    "AxleRuntimeBundlePlanner", "DependencyDeclaration", "EnhancedAssetConverter",
    "ExternalToolApproval", "PeExportEvidence", "PlannedConfiguration",
    "ResolvedWheelMap", "RuntimeDependency", "StoryRuntimeProfile",
    "StoryRuntimeValidationReceipt", "TargetBuildPlan",
    "TargetCapabilities", "VehicleAxleBuildInput", "compatibility_configuration",
    "inspect_story_runtime_binary", "resolve_runtime_wheel_map",
    "select_newest_compatible_runtime", "story_runtime_profile_report",
    "target_capabilities",
]
