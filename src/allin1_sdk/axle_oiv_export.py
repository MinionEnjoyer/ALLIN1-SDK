"""Safe OIV 2.2 transport for already-staged Story Mode axle builds.

No vehicle asset is regenerated here. The exporter reads only explicitly
declared files beneath one staging root and never writes to a GTA installation.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol, Sequence

from lxml import etree as ET
from PIL import Image

from allin1_sdk.axle_configurator import (
    AXLE_SCHEMA_VERSION,
    STEERING_GAIN_EPSILON,
    joaat_hex,
)
from allin1_sdk.axle_runtime_bundler import (
    RuntimeDependency,
    STORY_RUNTIME_NAME,
    TARGET_STORY_ENHANCED,
    TARGET_STORY_LEGACY,
)


OIV_FORMAT_VERSION = "2.2"
OIV_BUNDLE_SCHEMA_VERSION = 1
MODE_VEHICLE_ONLY = "vehicle-only"
MODE_RUNTIME_ONLY = "runtime-only"
MODE_SELF_CONTAINED = "self-contained"
OIV_MODES = (MODE_VEHICLE_ONLY, MODE_RUNTIME_ONLY, MODE_SELF_CONTAINED)
COMPRESSION_STORED = "stored"
COMPRESSION_DEFLATED = "deflated"
SELF_CONTAINED_WARNING = (
    "Convenient single-package install. May replace an existing Vehicle "
    "Workbench axle runtime."
)
NEWER_RUNTIME_WARNING = (
    "OIV add operations cannot guarantee that an older bundled ASI will not "
    "overwrite a newer installed runtime."
)
ENHANCED_UNVALIDATED_MESSAGE = (
    "Enhanced OIV export is not validated. Export an OpenRPF-ready ZIP instead."
)

_FIXED_ZIP_DATE = (1980, 1, 1, 0, 0, 0)
_SAFE_PACKAGE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{1,95}$")
_SAFE_DLC_PACK = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_SAFE_MODEL = re.compile(r"^[a-z0-9][a-z0-9_]{0,95}$")
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+]([0-9A-Za-z.-]+))?$")
_GUID = re.compile(r"^\{[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}\}$")
_ARGB = re.compile(r"^\$[0-9A-F]{8}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_INVALID_XML = re.compile(
    "[\x00-\x08\x0B\x0C\x0E-\x1F\uD800-\uDFFF\uFFFE\uFFFF]"
)
_RESERVED_WINDOWS = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
})
_FORBIDDEN_BINARIES = frozenset({
    "scripthookv.dll", "scripthookv.net.dll", "dinput8.dll", "xinput1_3.dll",
    "openiv.exe", "openiv.asi", "openrpf.asi", "alchemist.exe",
})
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_CONFIGURATION_BYTES = 4 * 1024 * 1024
_MAX_VALIDATION_REPORT_BYTES = 8 * 1024 * 1024
_MAX_ICON_BYTES = 4 * 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_object_file(path: Path, label: str, maximum_bytes: int) -> dict[str, Any]:
    try:
        size = path.stat().st_size
        if not 0 < size <= maximum_bytes:
            raise ValueError(f"{label} is empty or exceeds its guarded size limit")
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} root must be an object")
    return payload


def _xml_text(value: str, label: str, *, limit: int = 4000) -> str:
    text = str(value).strip()
    if not text or len(text) > limit or _INVALID_XML.search(text):
        raise ValueError(f"{label} must contain 1-{limit} valid XML characters")
    return text


def _version(value: str) -> str:
    text = str(value).strip()
    if not _SEMVER.fullmatch(text):
        raise ValueError("Package version must use semantic major.minor.patch form")
    return text


def _version_key(value: str) -> tuple[int, int, int, int, str]:
    match = _SEMVER.fullmatch(_version(value))
    assert match is not None
    suffix = match.group(4) or ""
    return (
        int(match.group(1)), int(match.group(2)), int(match.group(3)),
        1 if not suffix else 0, suffix,
    )


def _safe_id(value: str, label: str, pattern: re.Pattern[str]) -> str:
    text = str(value).strip().casefold()
    if not pattern.fullmatch(text):
        raise ValueError(f"{label} is not a safe stable identifier")
    return text


def _safe_relative(value: str, label: str) -> str:
    text = str(value).strip().replace("\\", "/")
    parts = text.split("/")
    if (
        not text or text.startswith("/") or ":" in parts[0]
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError(f"{label} must be a safe relative path")
    for part in parts:
        stem = part.rstrip(" .").split(".", 1)[0].casefold()
        if not stem or stem in _RESERVED_WINDOWS:
            raise ValueError(f"{label} contains a reserved Windows path component")
        if any(character in part for character in '<>:"|?*'):
            raise ValueError(f"{label} contains invalid path characters")
    return PurePosixPath(*parts).as_posix()


def _stage_file(root: Path, relative: str, label: str) -> Path:
    safe = _safe_relative(relative, label)
    candidate = (root / Path(*PurePosixPath(safe).parts)).resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise ValueError(f"{label} escapes the Story staging directory")
    if not candidate.is_file() or candidate.is_symlink():
        raise ValueError(f"{label} is missing or unsafe: {safe}")
    return candidate


def _guid_text(value: uuid.UUID | str) -> str:
    try:
        parsed = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value).strip("{}"))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Package GUID is invalid") from exc
    return "{" + str(parsed).upper() + "}"


class OivIdentityStore(Protocol):
    def resolve(self, project_id: str, variant: str) -> uuid.UUID: ...


class JsonOivIdentityStore:
    """Persist a package GUID once without tying it to build content hashes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve(strict=False)

    def resolve(self, project_id: str, variant: str) -> uuid.UUID:
        project = _safe_id(project_id, "Project id", _SAFE_PACKAGE_ID)
        key = f"{project}:{_safe_relative(variant, 'Package identity variant')}"
        data: dict[str, str] = {}
        if self.path.exists() or self.path.is_symlink():
            if not self.path.is_file() or self.path.is_symlink():
                raise ValueError("OIV identity store is unsafe")
            try:
                payload = json.loads(self.path.read_text("utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ValueError("OIV identity store is invalid") from exc
            if not isinstance(payload, dict) or payload.get("schema_version") != 1 \
                    or not isinstance(payload.get("identities"), dict):
                raise ValueError("OIV identity store has an unsupported schema")
            data = dict(payload["identities"])
        if key in data:
            return uuid.UUID(str(data[key]).strip("{}"))
        generated = uuid.uuid4()
        data[key] = str(generated)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            temporary.write_text(
                json.dumps({"schema_version": 1, "identities": data}, indent=2)
                + "\n",
                encoding="utf-8",
            )
            if self.path.exists():
                temporary.replace(self.path)
            else:
                try:
                    os.link(temporary, self.path)
                    temporary.unlink()
                except (FileExistsError, OSError):
                    if self.path.exists():
                        temporary.unlink(missing_ok=True)
                        return self.resolve(project_id, variant)
                    temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)
        return generated


class OivTargetProfile(Protocol):
    target_id: str
    edition: str
    installer_name: str
    asset_format: str
    integration_validated: bool
    supported_game_builds: tuple[str, ...]
    limitations: tuple[str, ...]

    @property
    def supports_oiv(self) -> bool: ...


@dataclass(frozen=True)
class LegacyOivTargetProfile:
    target_id: str = TARGET_STORY_LEGACY
    edition: str = "legacy"
    installer_name: str = "OpenIV Package Installer"
    asset_format: str = "legacy-rpf7-gen8"
    # The 2.2 transport is regression-tested, but no synthetic unit fixture is
    # allowed to masquerade as an installer + in-game acceptance run.
    transport_validated: bool = True
    integration_validated: bool = False
    supported_game_builds: tuple[str, ...] = ("current-scripthookv-compatible",)
    limitations: tuple[str, ...] = (
        "OIV XML insertion idempotence depends on the selected installer.",
        "The installer cannot enforce semantic runtime dependency versions.",
        "This SDK build has not recorded a real installer and in-game Legacy acceptance run.",
    )

    @property
    def supports_oiv(self) -> bool:
        return self.transport_validated


@dataclass(frozen=True)
class EnhancedOivTargetProfile:
    target_id: str = TARGET_STORY_ENHANCED
    edition: str = "enhanced"
    installer_name: str = "unvalidated"
    asset_format: str = "gen9-required"
    integration_validated: bool = False
    supported_game_builds: tuple[str, ...] = ()
    required_asset_loader: str = "user-configured Enhanced asset loader"
    archive_paths: tuple[str, ...] = ()
    installation_rules: tuple[str, ...] = ()
    runtime_profile_id: str | None = None
    acceptance_receipt_sha256: str | None = None
    limitations: tuple[str, ...] = (ENHANCED_UNVALIDATED_MESSAGE,)

    @property
    def supports_oiv(self) -> bool:
        return bool(
            self.integration_validated
            and self.installer_name != "unvalidated"
            and self.supported_game_builds
            and self.archive_paths
            and self.installation_rules
            and self.runtime_profile_id is not None
            and _SAFE_PACKAGE_ID.fullmatch(self.runtime_profile_id.casefold()) is not None
            and self.acceptance_receipt_sha256 is not None
            and _SHA256.fullmatch(self.acceptance_receipt_sha256.casefold()) is not None
        )


@dataclass(frozen=True)
class OivPackageMetadata:
    project_id: str
    package_id: str
    name: str
    version: str
    author: str
    description: str
    workbench_version: str
    support_url: str | None = None
    license_name: str | None = None
    package_guid: str | None = None
    header_color: str = "$FF2D9C50"
    icon_color: str = "$FF1F7F42"

    def validate(self) -> "OivPackageMetadata":
        _safe_id(self.project_id, "Project id", _SAFE_PACKAGE_ID)
        _safe_id(self.package_id, "Package id", _SAFE_PACKAGE_ID)
        _version(self.version)
        _version(self.workbench_version)
        for value, label, limit in (
            (self.name, "Package name", 200),
            (self.author, "Author", 200),
            (self.description, "Description", 4000),
        ):
            _xml_text(value, label, limit=limit)
        if self.support_url is not None:
            if (
                len(self.support_url) > 2048
                or _INVALID_XML.search(self.support_url)
                or not self.support_url.startswith(("https://", "http://"))
            ):
                raise ValueError("Support URL must be a valid bounded HTTP(S) URL")
        if self.license_name is not None:
            _xml_text(self.license_name, "License", limit=200)
        if self.package_guid is not None:
            _guid_text(self.package_guid)
        for color in (self.header_color, self.icon_color):
            if not _ARGB.fullmatch(color):
                raise ValueError("OIV colors must use $AARRGGBB format")
        return self


@dataclass(frozen=True)
class StagedVehicleDlc:
    dlc_pack_name: str
    archive_path: str
    vehicle_models: tuple[str, ...]
    asset_edition: str = "legacy"

    def validate(self, target_id: str) -> "StagedVehicleDlc":
        _safe_id(self.dlc_pack_name, "DLC pack name", _SAFE_DLC_PACK)
        _safe_relative(self.archive_path, "Staged DLC archive")
        if not self.vehicle_models:
            raise ValueError("A staged vehicle DLC must declare at least one model")
        for model in self.vehicle_models:
            _safe_id(model, "Vehicle model", _SAFE_MODEL)
        required_edition = "legacy" if target_id == TARGET_STORY_LEGACY else "enhanced"
        if self.asset_edition.casefold() != required_edition:
            raise ValueError(
                f"{self.asset_edition} vehicle assets cannot be placed in {target_id}"
            )
        return self


@dataclass(frozen=True)
class StagedAxleConfiguration:
    model_name: str
    model_hash: str
    source_path: str
    schema_version: int = AXLE_SCHEMA_VERSION
    minimum_runtime_version: str = "1.0.0"

    def validate(self) -> "StagedAxleConfiguration":
        _safe_id(self.model_name, "Configuration model", _SAFE_MODEL)
        _safe_relative(self.source_path, "Staged axle configuration")
        if not re.fullmatch(r"0x[0-9A-Fa-f]{8}", self.model_hash):
            raise ValueError("Axle configuration model hash must use 0x plus eight hex digits")
        if self.schema_version < 1:
            raise ValueError("Axle schema version must be positive")
        _version(self.minimum_runtime_version)
        return self


@dataclass(frozen=True)
class StagedRuntime:
    asi_path: str
    version: str
    target_id: str
    supported_game_builds: tuple[str, ...]
    maximum_schema_version: int
    binary_sha256: str
    build_date: str
    profile_id: str
    validation_receipt_path: str
    validation_receipt_sha256: str
    package_eligible: bool
    redistribution_allowed: bool
    license_name: str
    architecture: str = "x64"
    required_scripthook_version: str = "current compatible release"

    def validate(self, target_id: str) -> "StagedRuntime":
        if self.target_id != target_id:
            raise ValueError("Runtime ASI edition does not match the OIV target")
        if PurePosixPath(_safe_relative(self.asi_path, "Staged runtime ASI")).name \
                != f"{STORY_RUNTIME_NAME}.asi":
            raise ValueError("The generic axle runtime must keep its shared ASI filename")
        _version(self.version)
        if not self.supported_game_builds:
            raise ValueError("Runtime must declare supported game builds")
        if self.maximum_schema_version < 1:
            raise ValueError("Runtime maximum schema version must be positive")
        if not _SHA256.fullmatch(self.binary_sha256.casefold()):
            raise ValueError("Runtime binary checksum is invalid")
        _safe_id(self.profile_id, "Runtime profile id", _SAFE_PACKAGE_ID)
        _safe_relative(
            self.validation_receipt_path, "Runtime validation receipt",
        )
        if not _SHA256.fullmatch(self.validation_receipt_sha256.casefold()):
            raise ValueError("Runtime validation receipt checksum is invalid")
        if not self.package_eligible:
            raise ValueError("Runtime profile is not package eligible")
        if not self.redistribution_allowed:
            raise ValueError("Runtime redistribution rights are not confirmed")
        _xml_text(self.license_name, "Runtime license", limit=200)
        _xml_text(self.build_date, "Runtime build date", limit=80)
        if self.architecture != "x64":
            raise ValueError("GTA V axle runtime architecture must be x64")
        _xml_text(
            self.required_scripthook_version,
            "Required ScriptHookV version", limit=200,
        )
        return self


@dataclass(frozen=True)
class OivExportRequest:
    staging_root: Path
    target_profile: OivTargetProfile
    mode: str
    metadata: OivPackageMetadata
    vehicle_dlcs: tuple[StagedVehicleDlc, ...] = ()
    axle_configurations: tuple[StagedAxleConfiguration, ...] = ()
    runtime: StagedRuntime | None = None
    include_documentation: bool = True
    icon_path: Path | None = None
    compression: str = COMPRESSION_DEFLATED
    confirm_self_contained: bool = False
    known_existing_runtime_version: str | None = None
    diagnostic_report_path: Path | None = None


@dataclass(frozen=True)
class OivContentFile:
    archive_member: str
    install_destination: str
    kind: str
    size: int
    sha256: str
    source_path: Path | None = None
    payload: bytes | None = None
    replaces_existing: bool = False

    def bytes(self) -> bytes:
        if self.payload is not None:
            return self.payload
        assert self.source_path is not None
        data = self.source_path.read_bytes()
        if len(data) != self.size or _sha256_bytes(data) != self.sha256:
            raise ValueError(f"Staged OIV source changed: {self.archive_member}")
        return data


@dataclass(frozen=True)
class OivContentPlan:
    request: OivExportRequest
    package_guid: str
    files: tuple[OivContentFile, ...]
    dlc_entries: tuple[str, ...]
    dependencies: tuple[Mapping[str, Any], ...]
    warnings: tuple[str, ...]
    package_manifest: Mapping[str, Any]

    def installation_preview(self) -> dict[str, Any]:
        return {
            "target": self.request.target_profile.target_id,
            "edition": self.request.target_profile.edition,
            "mode": self.request.mode,
            "asset_format": self.request.target_profile.asset_format,
            "files_added": [item.install_destination for item in self.files if not item.replaces_existing],
            "files_replaced": [item.install_destination for item in self.files if item.replaces_existing],
            "archives_modified": ["update\\update.rpf"] if self.dlc_entries else [],
            "xml_entries_added": [f"dlcpacks:/{name}/" for name in self.dlc_entries],
            "dependencies": [dict(item) for item in self.dependencies],
            "warnings": list(self.warnings),
            "third_party_binaries_included": False,
        }


class OivContentPlanner:
    def __init__(self, identity_store: OivIdentityStore) -> None:
        self.identity_store = identity_store

    def plan(self, request: OivExportRequest) -> OivContentPlan:
        metadata = request.metadata.validate()
        package_id = _safe_id(metadata.package_id, "Package id", _SAFE_PACKAGE_ID)
        if request.mode not in OIV_MODES:
            raise ValueError("Unsupported OIV package mode")
        if request.target_profile.target_id not in {
            TARGET_STORY_LEGACY, TARGET_STORY_ENHANCED,
        }:
            raise ValueError("OIV packages may target Story Mode only")
        if not request.target_profile.supports_oiv:
            raise ValueError(ENHANCED_UNVALIDATED_MESSAGE)
        if request.compression not in {COMPRESSION_STORED, COMPRESSION_DEFLATED}:
            raise ValueError("OIV compression must be stored or deflated")
        root = Path(request.staging_root).expanduser().resolve(strict=False)
        if not root.is_dir() or root.is_symlink():
            raise ValueError("Story staging directory is missing or unsafe")
        stage_manifest = self._validate_stage_target(
            root, request.target_profile.target_id,
        )

        needs_vehicle = request.mode in {MODE_VEHICLE_ONLY, MODE_SELF_CONTAINED}
        needs_runtime = request.mode in {MODE_RUNTIME_ONLY, MODE_SELF_CONTAINED}
        if needs_vehicle and (not request.vehicle_dlcs or not request.axle_configurations):
            raise ValueError("Vehicle OIV modes require staged DLC and axle configuration files")
        if not needs_vehicle and (request.vehicle_dlcs or request.axle_configurations):
            raise ValueError("Runtime-only OIV cannot own vehicle DLC or model configurations")
        if needs_runtime and request.runtime is None:
            raise ValueError("Selected OIV mode requires a staged generic axle runtime")
        if not needs_runtime and request.runtime is not None:
            raise ValueError("Vehicle-only OIV must not contain an ASI runtime")
        if request.mode == MODE_SELF_CONTAINED and not request.confirm_self_contained:
            raise ValueError("Self-contained OIV export requires explicit confirmation")
        if request.known_existing_runtime_version is not None:
            _version(request.known_existing_runtime_version)
            if request.runtime is not None and _version_key(
                request.known_existing_runtime_version
            ) > _version_key(request.runtime.version):
                raise ValueError(
                    "Selected axle runtime is older than the known staged/installed runtime"
                )

        guid = (
            _guid_text(metadata.package_guid)
            if metadata.package_guid else _guid_text(self.identity_store.resolve(
                metadata.project_id,
                f"{request.target_profile.target_id}/{request.mode}",
            ))
        )
        files: list[OivContentFile] = []
        dlc_entries: list[str] = []
        dependencies: list[Mapping[str, Any]] = []
        warnings = list(request.target_profile.limitations)
        if needs_vehicle:
            self._append_vehicle_content(
                request, root, files, dlc_entries, stage_manifest,
            )
            warnings.extend((
                "An existing DLC pack with the same stable name may be replaced or conflict.",
                "An existing model-specific axle configuration may be replaced on upgrade.",
            ))
        if needs_runtime:
            assert request.runtime is not None
            self._append_runtime_content(request, root, files)
            dependencies.append(self._scripthook_dependency(request.target_profile.target_id))
        else:
            minimum = max(
                (item.minimum_runtime_version for item in request.axle_configurations),
                key=_version_key,
            )
            dependencies.append({
                "id": "vehicle-workbench-axle-runtime",
                "minimumVersion": minimum,
                "target": request.target_profile.target_id,
                "required": True,
                "bundled": False,
            })
            warnings.extend((
                "Runtime not included; install the matching Vehicle Workbench axle runtime separately.",
                "OIV cannot enforce semantic runtime dependency versions.",
            ))
        if request.mode == MODE_SELF_CONTAINED:
            warnings.extend((SELF_CONTAINED_WARNING, NEWER_RUNTIME_WARNING))

        if request.include_documentation:
            readme = self._readme(request, dependencies, warnings).encode("utf-8")
            files.append(self._payload_file(
                f"content/docs/{package_id}.README.txt",
                f"{STORY_RUNTIME_NAME}\\docs\\{package_id}.README.txt",
                "documentation", readme,
            ))

        self._validate_content_collisions(files, dlc_entries)
        manifest_destination = (
            f"{STORY_RUNTIME_NAME}\\packages\\{package_id}.manifest.json"
        )
        owned = [item.install_destination for item in files] + [manifest_destination]
        checksums = {
            item.install_destination: item.sha256 for item in files
        }
        package_manifest: dict[str, Any] = {
            "bundleSchemaVersion": OIV_BUNDLE_SCHEMA_VERSION,
            "packageGuid": guid,
            "packageId": package_id,
            "projectId": metadata.project_id,
            "packageType": request.mode,
            "packageVersion": metadata.version,
            "target": request.target_profile.target_id,
            "workbenchVersion": metadata.workbench_version,
            "axleSchemaVersion": AXLE_SCHEMA_VERSION,
            "dlcPackNames": list(dlc_entries),
            "vehicleModels": sorted({
                model for dlc in request.vehicle_dlcs for model in dlc.vehicle_models
            }),
            "axleConfigurations": [
                {
                    "modelName": item.model_name,
                    "modelHash": item.model_hash.upper().replace("0X", "0x"),
                    "path": f"{STORY_RUNTIME_NAME}/configs/{item.model_name}.json",
                    "schemaVersion": item.schema_version,
                }
                for item in request.axle_configurations
            ],
            "dependencies": [dict(item) for item in dependencies],
            "ownedFiles": owned,
            "checksums": checksums,
            "manifestSelfChecksumExcluded": True,
            "uninstallPolicy": {
                "sharedRuntimeRemovedByVehiclePackage": False,
                "thirdPartyDependenciesRemoved": False,
                "broadDirectoryDeletionAllowed": False,
            },
        }
        manifest_payload = json.dumps(package_manifest, indent=2) .encode("utf-8") + b"\n"
        files.append(self._payload_file(
            f"content/manifests/{package_id}.manifest.json",
            manifest_destination,
            "workbench_manifest", manifest_payload,
        ))
        self._validate_content_collisions(files, dlc_entries)
        return OivContentPlan(
            request=request,
            package_guid=guid,
            files=tuple(files),
            dlc_entries=tuple(dlc_entries),
            dependencies=tuple(dependencies),
            warnings=tuple(dict.fromkeys(warnings)),
            package_manifest=package_manifest,
        )

    def plan_enhanced_fallback(
        self, request: OivExportRequest,
    ) -> OivContentPlan:
        """Validate the manual Enhanced payload without claiming OIV support."""
        if request.target_profile.target_id != TARGET_STORY_ENHANCED:
            raise ValueError(
                "OpenRPF fallback is reserved for unvalidated Story Enhanced export"
            )
        if request.target_profile.supports_oiv:
            raise ValueError("Validated Enhanced profiles should use OIV export")
        validation_profile = EnhancedOivTargetProfile(
            installer_name="OpenRPF manual import",
            integration_validated=True,
            supported_game_builds=("manual-validation-required",),
            archive_paths=("manual-review",),
            installation_rules=("manual OpenRPF import",),
            runtime_profile_id="manual-preview-only",
            acceptance_receipt_sha256="0" * 64,
        )
        return self.plan(OivExportRequest(**{
            **request.__dict__, "target_profile": validation_profile,
        }))

    @staticmethod
    def _validate_stage_target(
        root: Path, target_id: str,
    ) -> Mapping[str, Any]:
        manifest = root / "compatibility-manifest.json"
        if not manifest.is_file() or manifest.is_symlink():
            raise ValueError(
                "Story staging compatibility-manifest.json is required for OIV export"
            )
        payload = _json_object_file(
            manifest, "Story staging compatibility manifest", _MAX_MANIFEST_BYTES,
        )
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or payload.get("target") != target_id
            or payload.get("game_write_performed") is not False
        ):
            raise ValueError("Story staging target does not match the OIV target profile")
        return payload

    def _append_vehicle_content(
        self,
        request: OivExportRequest,
        root: Path,
        files: list[OivContentFile],
        dlc_entries: list[str],
        stage_manifest: Mapping[str, Any],
    ) -> None:
        raw_evidence = stage_manifest.get("vehicle_artifacts")
        if not isinstance(raw_evidence, list):
            raise ValueError(
                "Story staging manifest must contain hash-bound vehicle_artifacts evidence"
            )
        evidence_by_path: dict[str, Mapping[str, Any]] = {}
        for item in raw_evidence:
            if not isinstance(item, Mapping):
                raise ValueError("Story vehicle artifact evidence entries must be objects")
            path = _safe_relative(
                str(item.get("path", "")), "Story vehicle artifact evidence path",
            )
            if path.casefold() in evidence_by_path:
                raise ValueError("Duplicate Story vehicle artifact evidence path")
            evidence_by_path[path.casefold()] = item
        packs: set[str] = set()
        declared_models: set[str] = set()
        for dlc in request.vehicle_dlcs:
            dlc.validate(request.target_profile.target_id)
            pack = dlc.dlc_pack_name.casefold()
            if pack in packs:
                raise ValueError(f"Duplicate DLC pack name: {pack}")
            packs.add(pack)
            for raw_model in dlc.vehicle_models:
                model = raw_model.casefold()
                if model in declared_models:
                    raise ValueError(
                        f"Duplicate vehicle model declaration across DLC packs: {model}"
                    )
                declared_models.add(model)
            source = _stage_file(root, dlc.archive_path, "Staged DLC archive")
            with source.open("rb") as stream:
                magic = stream.read(4)
            if magic != b"RPF7":
                raise ValueError(
                    f"Staged vehicle DLC is not a Rockstar RPF7 archive: {dlc.archive_path}"
                )
            evidence = evidence_by_path.get(
                _safe_relative(dlc.archive_path, "Staged DLC archive").casefold(),
            )
            if evidence is None:
                raise ValueError(
                    f"Staged vehicle DLC has no build evidence: {dlc.archive_path}"
                )
            self._validate_vehicle_artifact_evidence(
                root, source, dlc, evidence, request.target_profile.asset_format,
            )
            files.append(self._source_file(
                source,
                f"content/dlcpacks/{pack}/dlc.rpf",
                f"update\\x64\\dlcpacks\\{pack}\\dlc.rpf",
                "vehicle_dlc",
                replaces=True,
            ))
            dlc_entries.append(pack)
        models: set[str] = set()
        hashes: set[str] = set()
        config_names: set[str] = set()
        dlc_models = {
            model.casefold() for dlc in request.vehicle_dlcs for model in dlc.vehicle_models
        }
        for config in request.axle_configurations:
            config.validate()
            model = config.model_name.casefold()
            digest = config.model_hash.casefold()
            filename = f"{model}.json"
            if model in models or filename in config_names:
                raise ValueError(f"Duplicate axle configuration filename: {filename}")
            if digest in hashes:
                raise ValueError(f"Duplicate axle configuration model hash: {config.model_hash}")
            if model not in dlc_models:
                raise ValueError(f"Axle configuration model is absent from staged DLC metadata: {model}")
            if config.schema_version > AXLE_SCHEMA_VERSION:
                raise ValueError("Axle configuration requires a newer unsupported schema")
            models.add(model)
            hashes.add(digest)
            config_names.add(filename)
            source = _stage_file(root, config.source_path, "Staged axle configuration")
            self._validate_config_payload(
                source, config, request.target_profile.target_id,
            )
            files.append(self._source_file(
                source,
                f"content/configs/{filename}",
                f"{STORY_RUNTIME_NAME}\\configs\\{filename}",
                "axle_configuration",
                replaces=True,
            ))

    @staticmethod
    def _validate_vehicle_artifact_evidence(
        root: Path,
        archive: Path,
        dlc: StagedVehicleDlc,
        evidence: Mapping[str, Any],
        expected_asset_format: str,
    ) -> None:
        archive_sha = _sha256_file(archive)
        if (
            str(evidence.get("sha256", "")).casefold() != archive_sha
            or str(evidence.get("asset_edition", "")).casefold()
            != dlc.asset_edition.casefold()
            or evidence.get("asset_format") != expected_asset_format
            or evidence.get("validation_status") != "validated"
        ):
            raise ValueError(
                f"Staged vehicle DLC build evidence does not match {dlc.dlc_pack_name}"
            )
        report_path = _safe_relative(
            str(evidence.get("validation_report", "")),
            "Story vehicle validation report",
        )
        report = _stage_file(root, report_path, "Story vehicle validation report")
        if str(evidence.get("validation_report_sha256", "")).casefold() \
                != _sha256_file(report):
            raise ValueError("Story vehicle validation report checksum does not match")
        payload = _json_object_file(
            report, "Story vehicle validation report", _MAX_VALIDATION_REPORT_BYTES,
        )
        safety = payload.get("safety") if isinstance(payload, dict) else None
        report_payload = payload.get("payload") if isinstance(payload, dict) else None
        editions = payload.get("editions") if isinstance(payload, dict) else None
        if (
            payload.get("operation") != "vehicle_addon_package_build"
            or payload.get("status") != "validated"
            or not isinstance(report_payload, dict)
            or str(report_payload.get("sha256", "")).casefold() != archive_sha
            or not isinstance(editions, list)
            or dlc.asset_edition.casefold() not in {
                str(item).casefold() for item in editions
            }
            or not isinstance(safety, dict)
            or safety.get("source_unchanged") is not True
            or safety.get("output_was_new") is not True
            or safety.get("stock_game_files_modified") is not False
            or safety.get("manifest_payload_validated") is not True
        ):
            raise ValueError(
                "Story vehicle validation report does not prove the staged archive"
            )
        native_path = _safe_relative(
            str(evidence.get("native_validation_report", "")),
            "Story native RPF validation report",
        )
        native_report = _stage_file(
            root, native_path, "Story native RPF validation report",
        )
        if str(evidence.get("native_validation_report_sha256", "")).casefold() \
                != _sha256_file(native_report):
            raise ValueError("Story native RPF validation report checksum does not match")
        native = _json_object_file(
            native_report,
            "Story native RPF validation report",
            _MAX_VALIDATION_REPORT_BYTES,
        )
        model_checks = native.get("model_assets") if isinstance(native, dict) else None
        required_metadata = (
            native.get("required_metadata") if isinstance(native, dict) else None
        )
        if (
            native.get("schema_version") != 1
            or native.get("operation") != "validate_story_vehicle_rpf"
            or native.get("status") != "validated"
            or str(native.get("archive_sha256", "")).casefold() != archive_sha
            or str(native.get("edition", "")).casefold()
            != dlc.asset_edition.casefold()
            or not isinstance(native.get("archive_count"), int)
            or native.get("archive_count", 0) < 1
            or not isinstance(native.get("entry_count"), int)
            or native.get("entry_count", 0) < 1
            or not isinstance(model_checks, dict)
            or set(model_checks) != {item.casefold() for item in dlc.vehicle_models}
            or any(
                not isinstance(check, dict)
                or check.get("yft") is not True
                or check.get("ytd") is not True
                for check in model_checks.values()
            )
            or not isinstance(required_metadata, dict)
            or any(
                required_metadata.get(name) is not True
                for name in ("vehicles.meta", "handling.meta", "carvariations.meta")
            )
        ):
            raise ValueError(
                "Native RPF validation does not prove the staged vehicle assets"
            )

    @staticmethod
    def _validate_config_payload(
        source: Path,
        declared: StagedAxleConfiguration,
        target_id: str,
    ) -> None:
        payload = _json_object_file(
            source,
            f"Staged axle configuration for {declared.model_name}",
            _MAX_CONFIGURATION_BYTES,
        )
        model = str(payload.get("modelName", payload.get("vehicle_model", ""))).casefold()
        model_hash = str(payload.get("modelHash", payload.get("model_hash", ""))).casefold()
        schema = payload.get("schemaVersion", payload.get("schema_version"))
        if model != declared.model_name.casefold() \
                or model_hash != declared.model_hash.casefold() \
                or schema != declared.schema_version:
            raise ValueError(
                f"Staged axle configuration evidence does not match {declared.model_name}"
            )
        expected_hash = joaat_hex(declared.model_name).casefold()
        if model_hash != expected_hash:
            raise ValueError(
                f"Staged axle model hash does not match {declared.model_name}'s GTA joaat hash"
            )
        compatibility = payload.get("compatibility")
        if (
            not isinstance(compatibility, dict)
            or compatibility.get(target_id) is not True
        ):
            raise ValueError(
                f"Staged axle configuration does not explicitly enable {target_id}"
            )
        mapping = payload.get("wheelIndexMapping")
        axles = payload.get("axles")
        expected_count = payload.get("expectedWheelCount")
        configuration_id = payload.get("configurationId")
        minimum_runtime = payload.get("minimumRuntimeVersion")
        if (
            not isinstance(mapping, dict)
            or not isinstance(mapping.get("by_bone"), dict)
            or not isinstance(axles, list)
            or not 2 <= len(axles) <= 5
            or isinstance(expected_count, bool)
            or not isinstance(expected_count, int)
            or not isinstance(configuration_id, str)
            or not _SAFE_PACKAGE_ID.fullmatch(configuration_id.casefold())
            or not isinstance(minimum_runtime, str)
            or not _SEMVER.fullmatch(minimum_runtime)
        ):
            raise ValueError(
                "Staged Story axle configuration must be the target-resolved bundler output"
            )
        by_bone = mapping["by_bone"]
        resolved_indices: list[int] = []
        pair_order = (
            ("wheel_lf", "wheel_rf"),
            ("wheel_lm1", "wheel_rm1"),
            ("wheel_lm2", "wheel_rm2"),
            ("wheel_lm3", "wheel_rm3"),
            ("wheel_lr", "wheel_rr"),
        )
        expected_pairs = (pair_order[0], *pair_order[1:len(axles) - 1], pair_order[-1])
        expected_bones = {
            bone for pair in expected_pairs for bone in pair
        }
        if minimum_runtime != declared.minimum_runtime_version:
            raise ValueError(
                "Staged axle configuration minimumRuntimeVersion does not match "
                "its declared runtime dependency"
            )
        if set(by_bone) != expected_bones:
            raise ValueError(
                "Staged axle wheelIndexMapping must contain exactly the configured "
                "canonical wheel bones"
            )
        for position, axle in enumerate(axles):
            if not isinstance(axle, dict):
                raise ValueError("Staged Story axle rows must be objects")
            indices = axle.get("wheelIndices")
            left = str(axle.get("leftBone", "")).casefold()
            right = str(axle.get("rightBone", "")).casefold()
            role = axle.get("role")
            steered = axle.get("steered")
            steering_gain = axle.get(
                "steeringGain", 1.0 if steered is True else 0.0,
            )
            legacy_gain = 1.0 if steered is True else 0.0
            valid_steering_gain = (
                not isinstance(steering_gain, bool)
                and isinstance(steering_gain, (int, float))
                and math.isfinite(float(steering_gain))
                and -1.0 <= float(steering_gain) <= 1.0
                and (
                    steered is True
                    or abs(float(steering_gain)) <= STEERING_GAIN_EPSILON
                )
                and (
                    schema != AXLE_SCHEMA_VERSION
                    or abs(float(steering_gain) - legacy_gain)
                    <= STEERING_GAIN_EPSILON
                )
            )
            expected_role = (
                "front" if position == 0
                else "rear" if position + 1 == len(axles)
                else {"middle", "tag"}
            )
            if (
                not isinstance(indices, list) or len(indices) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) for value in indices)
                or axle.get("order") != position
                or (
                    role != expected_role
                    if isinstance(expected_role, str)
                    else role not in expected_role
                )
                or (left, right) != expected_pairs[position]
                or not isinstance(steered, bool)
                or not valid_steering_gain
                or not isinstance(axle.get("powered"), bool)
                or by_bone.get(left) != indices[0]
                or by_bone.get(right) != indices[1]
            ):
                raise ValueError(
                    "Staged Story axle rows do not satisfy the runtime schema and mapping"
                )
            resolved_indices.extend(indices)
        if (
            expected_count != len(resolved_indices)
            or sorted(resolved_indices) != list(range(expected_count))
        ):
            raise ValueError(
                "Staged Story axle runtime indices do not match expectedWheelCount"
            )

    def _append_runtime_content(
        self,
        request: OivExportRequest,
        root: Path,
        files: list[OivContentFile],
    ) -> None:
        assert request.runtime is not None
        runtime = request.runtime.validate(request.target_profile.target_id)
        if runtime.maximum_schema_version < AXLE_SCHEMA_VERSION:
            raise ValueError("Runtime supports fewer axle schema features than this project")
        incompatible = [
            item.model_name for item in request.axle_configurations
            if item.schema_version > runtime.maximum_schema_version
            or _version_key(item.minimum_runtime_version) > _version_key(runtime.version)
        ]
        if incompatible:
            raise ValueError(
                "Bundled runtime is incompatible with axle configurations: "
                + ", ".join(incompatible)
            )
        source = _stage_file(root, runtime.asi_path, "Staged runtime ASI")
        receipt = _stage_file(
            root, runtime.validation_receipt_path,
            "Staged runtime validation receipt",
        )
        if _sha256_file(source) != runtime.binary_sha256.casefold():
            raise ValueError("Staged runtime checksum does not match its build metadata")
        if _sha256_file(receipt) != runtime.validation_receipt_sha256.casefold():
            raise ValueError("Staged runtime validation receipt checksum does not match")
        # Re-run the same PE/export/receipt gate immediately before packaging;
        # a caller-authored runtime.json and renamed arbitrary file are never
        # sufficient evidence.
        RuntimeDependency(
            name=STORY_RUNTIME_NAME.casefold(),
            version=runtime.version,
            maximum_schema_version=runtime.maximum_schema_version,
            target_id=runtime.target_id,
            supported_game_builds=runtime.supported_game_builds,
            configuration_destination=f"{STORY_RUNTIME_NAME}/configs",
            binary_path=source,
            binary_sha256=runtime.binary_sha256,
            profile_id=runtime.profile_id,
            package_eligible=runtime.package_eligible,
            validation_receipt_path=receipt,
            validation_receipt_sha256=runtime.validation_receipt_sha256,
            license_name=runtime.license_name,
            redistribution_allowed=runtime.redistribution_allowed,
        ).validate()
        files.append(self._source_file(
            source,
            f"content/runtime/{STORY_RUNTIME_NAME}.asi",
            f"{STORY_RUNTIME_NAME}.asi",
            "generic_axle_runtime",
            replaces=True,
        ))
        files.append(self._source_file(
            receipt,
            "content/runtime/validation-receipt.json",
            f"{STORY_RUNTIME_NAME}\\validation-receipt.json",
            "runtime_validation_receipt",
            replaces=True,
        ))
        metadata = {
            "schema_version": 1,
            "runtime_name": STORY_RUNTIME_NAME,
            "runtime_version": runtime.version,
            "target": runtime.target_id,
            "supported_game_builds": list(runtime.supported_game_builds),
            "maximum_config_schema": runtime.maximum_schema_version,
            "binary_sha256": runtime.binary_sha256.casefold(),
            "profile_id": runtime.profile_id,
            "package_eligible": runtime.package_eligible,
            "validation_receipt_sha256": runtime.validation_receipt_sha256.casefold(),
            "license": runtime.license_name,
            "redistribution_allowed": runtime.redistribution_allowed,
            "build_date": runtime.build_date,
            "architecture": runtime.architecture,
            "required_scripthook_version": runtime.required_scripthook_version,
            "scripthook_bundled": False,
            "online_loading_supported": False,
        }
        files.append(self._payload_file(
            "content/runtime/runtime.json",
            f"{STORY_RUNTIME_NAME}\\runtime.json",
            "runtime_metadata",
            json.dumps(metadata, indent=2).encode("utf-8") + b"\n",
            replaces=True,
        ))
        for folder in ("configs", "logs"):
            payload = (
                "This directory is owned cooperatively. Do not delete unrelated files.\n"
            ).encode("utf-8")
            files.append(self._payload_file(
                f"content/runtime/{folder}-README.txt",
                f"{STORY_RUNTIME_NAME}\\{folder}\\README.txt",
                "runtime_directory_notice", payload,
            ))

    @staticmethod
    def _source_file(
        source: Path,
        member: str,
        destination: str,
        kind: str,
        *,
        replaces: bool = False,
    ) -> OivContentFile:
        return OivContentFile(
            archive_member=_safe_relative(member, "OIV archive member"),
            install_destination=_safe_relative(destination, "OIV install destination").replace("/", "\\"),
            kind=kind,
            size=source.stat().st_size,
            sha256=_sha256_file(source),
            source_path=source,
            replaces_existing=replaces,
        )

    @staticmethod
    def _payload_file(
        member: str,
        destination: str,
        kind: str,
        payload: bytes,
        *,
        replaces: bool = False,
    ) -> OivContentFile:
        return OivContentFile(
            archive_member=_safe_relative(member, "OIV archive member"),
            install_destination=_safe_relative(destination, "OIV install destination").replace("/", "\\"),
            kind=kind,
            size=len(payload),
            sha256=_sha256_bytes(payload),
            payload=payload,
            replaces_existing=replaces,
        )

    @staticmethod
    def _scripthook_dependency(target_id: str) -> Mapping[str, Any]:
        return {
            "id": "scripthookv-enhanced" if target_id == TARGET_STORY_ENHANCED else "scripthookv",
            "required": True,
            "bundled": False,
            "userProvided": True,
            "redistribution": "prohibited-or-unknown",
            "officialUrl": (
                "https://www.dev-c.com/post/scripthookv-enhanced"
                if target_id == TARGET_STORY_ENHANCED
                else "https://www.dev-c.com/gtav/scripthookv/"
            ),
        }

    @staticmethod
    def _readme(
        request: OivExportRequest,
        dependencies: Sequence[Mapping[str, Any]],
        warnings: Sequence[str],
    ) -> str:
        lines = [
            request.metadata.name,
            "=" * len(request.metadata.name),
            "",
            request.metadata.description,
            "",
            f"Target: {request.target_profile.target_id}",
            f"Package mode: {request.mode}",
            f"Asset format: {request.target_profile.asset_format}",
            "Install into Story Mode only. Never load this package in GTA Online.",
            "",
            "Dependencies:",
        ]
        for dependency in dependencies:
            lines.append(
                f"- {dependency['id']} (bundled: {str(dependency.get('bundled', False)).lower()})"
            )
            if dependency.get("officialUrl"):
                lines.append(f"  {dependency['officialUrl']}")
        lines.extend(("", "Warnings:"))
        lines.extend(f"- {warning}" for warning in warnings)
        lines.extend((
            "",
            "Uninstall only this package's exact DLC archive, configuration, manifest,",
            "and documentation. Do not delete the shared axle runtime, other configs,",
            "ScriptHookV, an ASI loader, or broad directories.",
        ))
        return "\n".join(lines) + "\n"

    @staticmethod
    def _validate_content_collisions(
        files: Sequence[OivContentFile], dlc_entries: Sequence[str],
    ) -> None:
        members: set[str] = set()
        destinations: set[str] = set()
        for item in files:
            member = item.archive_member.casefold()
            destination = item.install_destination.casefold()
            if member in members:
                raise ValueError(f"Duplicate OIV content source entry: {item.archive_member}")
            if destination in destinations:
                raise ValueError(f"Case-insensitive OIV destination collision: {item.install_destination}")
            members.add(member)
            destinations.add(destination)
            name = PurePosixPath(item.archive_member).name.casefold()
            if name in _FORBIDDEN_BINARIES:
                raise ValueError(f"Forbidden third-party binary in OIV plan: {name}")
            if name == "fxmanifest.lua" or item.archive_member.casefold().endswith(".lua"):
                raise ValueError("FiveM content cannot be placed in a Story OIV")
            if item.archive_member.casefold().endswith((".exe", ".dll")):
                raise ValueError("Undeclared executable content is forbidden in OIV packages")
            if item.archive_member.casefold().endswith(".asi") \
                    and name != f"{STORY_RUNTIME_NAME}.asi".casefold():
                raise ValueError("Only the generic Vehicle Workbench axle ASI may be included")
        if len({value.casefold() for value in dlc_entries}) != len(dlc_entries):
            raise ValueError("Duplicate dlclist.xml entry in OIV plan")


class OivAssemblyWriter:
    @staticmethod
    def write(plan: OivContentPlan) -> bytes:
        metadata = plan.request.metadata
        description = metadata.description
        if plan.request.mode == MODE_VEHICLE_ONLY:
            description += (
                " Requires the matching Vehicle Workbench axle runtime "
                "installed separately."
            )
        elif plan.request.mode == MODE_SELF_CONTAINED:
            description += " " + SELF_CONTAINED_WARNING + " " + NEWER_RUNTIME_WARNING
        root = ET.Element("package", {
            "version": OIV_FORMAT_VERSION,
            "id": plan.package_guid,
            "target": "Five",
        })
        meta = ET.SubElement(root, "metadata")
        ET.SubElement(meta, "name").text = metadata.name
        version = ET.SubElement(meta, "version")
        match = _SEMVER.fullmatch(metadata.version)
        assert match is not None
        ET.SubElement(version, "major").text = match.group(1)
        ET.SubElement(version, "minor").text = match.group(2)
        tag_parts = []
        if int(match.group(3)):
            tag_parts.append(f"Patch {match.group(3)}")
        if match.group(4):
            tag_parts.append(match.group(4))
        if tag_parts:
            ET.SubElement(version, "tag").text = " ".join(tag_parts)
        author = ET.SubElement(meta, "author")
        ET.SubElement(author, "displayName").text = metadata.author
        if metadata.support_url:
            ET.SubElement(author, "web").text = metadata.support_url
        description_attributes = (
            {"footerLink": metadata.support_url, "footerLinkTitle": "Support"}
            if metadata.support_url else {}
        )
        ET.SubElement(
            meta, "description", description_attributes,
        ).text = ET.CDATA(description)
        ET.SubElement(meta, "largeDescription").text = ET.CDATA(description)
        if metadata.license_name:
            ET.SubElement(meta, "licence").text = ET.CDATA(metadata.license_name)

        colors = ET.SubElement(root, "colors")
        header = ET.SubElement(colors, "headerBackground", {"useBlackTextColor": "False"})
        header.text = metadata.header_color
        ET.SubElement(colors, "iconBackground").text = metadata.icon_color

        content = ET.SubElement(root, "content")
        for item in sorted(plan.files, key=lambda value: value.archive_member.casefold()):
            relative_source = item.archive_member.removeprefix("content/")
            add = ET.SubElement(content, "add", {"source": relative_source})
            add.text = item.install_destination
        if plan.dlc_entries:
            archive = ET.SubElement(content, "archive", {
                "path": "update\\update.rpf",
                "createIfNotExist": "False",
                "type": "RPF7",
            })
            xml = ET.SubElement(archive, "xml", {"path": "common\\data\\dlclist.xml"})
            for pack in sorted(plan.dlc_entries):
                insert = ET.SubElement(xml, "add", {
                    "xpath": "/SMandatoryPacksData/Paths",
                    "append": "Last",
                })
                ET.SubElement(insert, "Item").text = f"dlcpacks:/{pack}/"
        ET.indent(root, space="  ")
        payload = ET.tostring(
            root, encoding="utf-8", xml_declaration=True, pretty_print=False,
        )
        OivPackageValidator.validate_assembly(payload, plan)
        return payload


class OivPackageValidator:
    @staticmethod
    def validate_assembly(payload: bytes, plan: OivContentPlan | None = None) -> None:
        parser = ET.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
        try:
            root = ET.fromstring(payload, parser=parser)
        except ET.XMLSyntaxError as exc:
            raise ValueError("Generated OIV assembly.xml is invalid") from exc
        if (
            root.tag != "package"
            or root.attrib.get("version") != OIV_FORMAT_VERSION
            or root.attrib.get("target") != "Five"
            or not _GUID.fullmatch(root.attrib.get("id", ""))
            or root.find("metadata") is None
            or root.find("colors") is None
            or root.find("content") is None
        ):
            raise ValueError("Generated OIV assembly.xml has an invalid 2.2 structure")
        if root.getroottree().docinfo.doctype:
            raise ValueError("OIV assembly.xml must not contain a DTD")
        metadata = root.find("metadata")
        colors = root.find("colors")
        content = root.find("content")
        assert metadata is not None and colors is not None and content is not None
        version = metadata.find("version")
        author = metadata.find("author")
        if (
            not (metadata.findtext("name") or "").strip()
            or version is None
            or not (version.findtext("major") or "").isdigit()
            or not (version.findtext("minor") or "").isdigit()
            or author is None
            or not (author.findtext("displayName") or "").strip()
            or metadata.find("description") is None
        ):
            raise ValueError("Generated OIV metadata is incomplete")
        header = colors.find("headerBackground")
        icon = colors.find("iconBackground")
        if (
            header is None
            or header.attrib.get("useBlackTextColor") not in {"True", "False"}
            or not _ARGB.fullmatch(header.text or "")
            or icon is None
            or not _ARGB.fullmatch(icon.text or "")
        ):
            raise ValueError("Generated OIV colors are invalid")
        seen_sources: set[str] = set()
        for node in content.findall("add"):
            source = _safe_relative(node.attrib.get("source", ""), "OIV assembly source")
            destination = _safe_relative(node.text or "", "OIV assembly destination")
            if source.casefold() in seen_sources:
                raise ValueError("Generated OIV assembly contains duplicate sources")
            seen_sources.add(source.casefold())
            if destination.casefold().endswith(("scripthookv.dll", "dinput8.dll")):
                raise ValueError("Generated OIV assembly references a forbidden dependency")
        for archive in content.findall("archive"):
            if (
                archive.attrib.get("type") != "RPF7"
                or archive.attrib.get("createIfNotExist") not in {"True", "False"}
            ):
                raise ValueError("Generated OIV archive command is invalid")
            _safe_relative(archive.attrib.get("path", ""), "OIV archive path")
            for xml in archive.findall("xml"):
                _safe_relative(xml.attrib.get("path", ""), "OIV XML target")
                for addition in xml.findall("add"):
                    if (
                        addition.attrib.get("append") not in {
                            "First", "Last", "Before", "After",
                        }
                        or not addition.attrib.get("xpath", "").startswith("/")
                        or len(addition) != 1
                        or addition[0].tag != "Item"
                    ):
                        raise ValueError("Generated OIV XML operation is invalid")
        if plan is not None:
            sources = {
                "content/" + node.attrib["source"].replace("\\", "/")
                for node in root.findall("content/add") if "source" in node.attrib
            }
            expected = {item.archive_member for item in plan.files}
            if sources != expected:
                raise ValueError("OIV assembly sources do not match the content plan")

    @staticmethod
    def validate_icon(path: str | Path) -> bytes:
        source = Path(path).expanduser().resolve(strict=False)
        if not source.is_file() or source.is_symlink():
            raise ValueError("OIV icon is missing or unsafe")
        if not 0 < source.stat().st_size <= _MAX_ICON_BYTES:
            raise ValueError("OIV icon is empty or exceeds the guarded size limit")
        try:
            with Image.open(source) as image:
                image.verify()
            with Image.open(source) as image:
                if image.format != "PNG" or image.size != (128, 128):
                    raise ValueError("OIV icon must be a 128x128 PNG")
        except (OSError, SyntaxError) as exc:
            raise ValueError("OIV icon is not a valid PNG") from exc
        return source.read_bytes()


@dataclass(frozen=True)
class OivExportResult:
    archive: Path
    archive_sha256: str
    assembly_sha256: str
    package_guid: str
    mode: str
    target_id: str
    members: tuple[str, ...]
    warnings: tuple[str, ...]
    installation_preview: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "archive": str(self.archive),
            "archive_sha256": self.archive_sha256,
            "assembly_sha256": self.assembly_sha256,
            "package_guid": self.package_guid,
            "mode": self.mode,
            "target": self.target_id,
            "members": list(self.members),
            "warnings": list(self.warnings),
            "installation_preview": dict(self.installation_preview),
            "game_write_performed": False,
        }


class OivPackageVerifier:
    @staticmethod
    def verify(path: str | Path, plan: OivContentPlan) -> tuple[str, ...]:
        archive_path = Path(path)
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None:
                raise ValueError("OIV ZIP integrity verification failed")
            names = tuple(item.filename for item in archive.infolist())
            expected = ["assembly.xml"]
            if plan.request.icon_path is not None:
                expected.append("icon.png")
            expected.append("content/")
            expected.extend(sorted(item.archive_member for item in plan.files))
            if names != tuple(expected):
                raise ValueError("OIV archive contains missing or unexpected members")
            OivPackageValidator.validate_assembly(archive.read("assembly.xml"), plan)
            allowed_compression = {
                zipfile.ZIP_STORED,
                zipfile.ZIP_DEFLATED,
            }
            if any(item.compress_type not in allowed_compression for item in archive.infolist()):
                raise ValueError("OIV archive uses an unsupported compression method")
            if any(item.flag_bits & 0x1 for item in archive.infolist()):
                raise ValueError("OIV archive members must not be encrypted")
            for item in plan.files:
                payload = archive.read(item.archive_member)
                if len(payload) != item.size or _sha256_bytes(payload) != item.sha256:
                    raise ValueError(f"OIV member checksum mismatch: {item.archive_member}")
            if plan.request.icon_path is not None:
                icon = archive.read("icon.png")
                if icon != OivPackageValidator.validate_icon(plan.request.icon_path):
                    raise ValueError("OIV icon verification failed")
        return names


class OivPackageBuilder:
    def __init__(self, identity_store: OivIdentityStore) -> None:
        self.planner = OivContentPlanner(identity_store)

    def build(self, request: OivExportRequest, destination: str | Path) -> OivExportResult:
        try:
            return self._build(request, destination)
        except Exception as exc:
            if request.diagnostic_report_path is not None:
                try:
                    self._write_failure_diagnostic(request, destination, exc)
                except Exception as diagnostic_exc:
                    exc.add_note(
                        "The requested OIV diagnostic report could not be written: "
                        f"{diagnostic_exc}"
                    )
            raise

    def _build(self, request: OivExportRequest, destination: str | Path) -> OivExportResult:
        if request.target_profile.target_id == TARGET_STORY_ENHANCED:
            # No public field combination may turn a future profile-shaped
            # object into support before the dedicated installer/in-game gate
            # is implemented and enabled in this build.
            raise ValueError(ENHANCED_UNVALIDATED_MESSAGE)
        output = Path(destination).expanduser().resolve(strict=False)
        if output.suffix.casefold() != ".oiv":
            raise ValueError("OIV export destination must use an .oiv filename")
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"OIV export destination already exists: {output}")
        stage_root = Path(request.staging_root).expanduser().resolve(strict=False)
        if output.is_relative_to(stage_root):
            raise ValueError("OIV output must be outside its Story staging directory")
        plan = self.planner.plan(request)
        assembly = OivAssemblyWriter.write(plan)
        icon = (
            OivPackageValidator.validate_icon(request.icon_path)
            if request.icon_path is not None else None
        )
        compression = (
            zipfile.ZIP_STORED
            if request.compression == COMPRESSION_STORED else zipfile.ZIP_DEFLATED
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.stem}-", suffix=".oiv", dir=output.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with zipfile.ZipFile(
                temporary, "w", compression=compression, compresslevel=(9 if compression == zipfile.ZIP_DEFLATED else None),
            ) as archive:
                self._write_member(archive, "assembly.xml", assembly, compression)
                if icon is not None:
                    self._write_member(archive, "icon.png", icon, compression)
                self._write_directory(archive, "content/", compression)
                for item in sorted(plan.files, key=lambda value: value.archive_member):
                    self._write_member(
                        archive, item.archive_member, item.bytes(), compression,
                    )
            members = OivPackageVerifier.verify(temporary, plan)
            archive_sha = _sha256_file(temporary)
            try:
                descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError as exc:
                raise FileExistsError(f"OIV export destination already exists: {output}") from exc
            os.close(descriptor)
            try:
                temporary.replace(output)
            except Exception:
                output.unlink(missing_ok=True)
                raise
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return OivExportResult(
            archive=output,
            archive_sha256=archive_sha,
            assembly_sha256=_sha256_bytes(assembly),
            package_guid=plan.package_guid,
            mode=request.mode,
            target_id=request.target_profile.target_id,
            members=members,
            warnings=plan.warnings,
            installation_preview=plan.installation_preview(),
        )

    @staticmethod
    def _write_failure_diagnostic(
        request: OivExportRequest,
        destination: str | Path,
        error: Exception,
    ) -> None:
        assert request.diagnostic_report_path is not None
        report = request.diagnostic_report_path.expanduser().resolve(strict=False)
        if report.suffix.casefold() != ".json":
            raise ValueError("OIV diagnostic report must use a .json filename")
        if report.exists() or report.is_symlink():
            raise FileExistsError(f"OIV diagnostic report already exists: {report}")
        stage_root = Path(request.staging_root).expanduser().resolve(strict=False)
        if report.is_relative_to(stage_root):
            raise ValueError("OIV diagnostic report must be outside the staging directory")
        report.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "operation": "build_axle_oiv",
            "status": "failed",
            "target": request.target_profile.target_id,
            "mode": request.mode,
            "package_id": request.metadata.package_id,
            "requested_output_name": Path(destination).name,
            "error_type": type(error).__name__,
            "error": str(error),
            "game_write_performed": False,
            "partial_output_retained": False,
        }
        descriptor = os.open(report, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, indent=2)
                stream.write("\n")
        except Exception:
            report.unlink(missing_ok=True)
            raise

    @staticmethod
    def _write_member(
        archive: zipfile.ZipFile, name: str, payload: bytes, compression: int,
    ) -> None:
        normalized = _safe_relative(name, "OIV archive member")
        info = zipfile.ZipInfo(normalized, date_time=_FIXED_ZIP_DATE)
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = compression
        archive.writestr(info, payload)

    @staticmethod
    def _write_directory(
        archive: zipfile.ZipFile, name: str, compression: int,
    ) -> None:
        if name != "content/":
            raise ValueError("Only the required OIV content directory may be emitted")
        info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_DATE)
        info.create_system = 3
        info.external_attr = (0o40755 << 16) | 0x10
        info.compress_type = compression
        archive.writestr(info, b"")

    def build_enhanced_fallback(
        self,
        request: OivExportRequest,
        destination: str | Path,
    ) -> Path:
        if request.target_profile.target_id != TARGET_STORY_ENHANCED:
            raise ValueError("OpenRPF fallback is reserved for unvalidated Story Enhanced export")
        if request.target_profile.supports_oiv:
            raise ValueError("Validated Enhanced profiles should use OIV export")
        output = Path(destination).expanduser().resolve(strict=False)
        if output.suffix.casefold() != ".zip":
            raise ValueError("Enhanced OpenRPF fallback must use a .zip filename")
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"Fallback destination already exists: {output}")
        stage_root = Path(request.staging_root).expanduser().resolve(strict=False)
        if output.is_relative_to(stage_root):
            raise ValueError("Fallback output must be outside its Story staging directory")
        # Reuse the same content/path validator without relabelling the output
        # as an installer-supported Enhanced OIV.
        plan = self.planner.plan_enhanced_fallback(request)
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.stem}-", suffix=".zip", dir=output.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            manual_files = []
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for item in sorted(plan.files, key=lambda value: value.install_destination.casefold()):
                    destination_name = _safe_relative(
                        item.install_destination, "OpenRPF fallback destination",
                    )
                    member = f"files/{destination_name}"
                    self._write_member(archive, member, item.bytes(), zipfile.ZIP_DEFLATED)
                    manual_files.append({
                        "member": member,
                        "destination": item.install_destination,
                        "sha256": item.sha256,
                    })
                manifest = {
                    "schema_version": 1,
                    "format": "openrpf-ready-manual",
                    "target": TARGET_STORY_ENHANCED,
                    "oiv_supported": False,
                    "warning": ENHANCED_UNVALIDATED_MESSAGE,
                    "files": manual_files,
                    "dlclist_entries": [f"dlcpacks:/{pack}/" for pack in plan.dlc_entries],
                    "game_write_performed": False,
                }
                self._write_member(
                    archive, "openrpf-manifest.json",
                    json.dumps(manifest, indent=2).encode("utf-8") + b"\n",
                    zipfile.ZIP_DEFLATED,
                )
                self._write_member(
                    archive, "README.txt",
                    (ENHANCED_UNVALIDATED_MESSAGE + "\nManual review is required before installation.\n").encode("utf-8"),
                    zipfile.ZIP_DEFLATED,
                )
            with zipfile.ZipFile(temporary) as archive:
                if archive.testzip() is not None or "assembly.xml" in archive.namelist():
                    raise ValueError("Enhanced fallback ZIP verification failed")
            try:
                descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError as exc:
                raise FileExistsError(f"Fallback destination already exists: {output}") from exc
            os.close(descriptor)
            try:
                temporary.replace(output)
            except Exception:
                output.unlink(missing_ok=True)
                raise
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return output


__all__ = [
    "COMPRESSION_DEFLATED", "COMPRESSION_STORED", "ENHANCED_UNVALIDATED_MESSAGE",
    "MODE_RUNTIME_ONLY", "MODE_SELF_CONTAINED", "MODE_VEHICLE_ONLY",
    "NEWER_RUNTIME_WARNING", "OIV_BUNDLE_SCHEMA_VERSION", "OIV_FORMAT_VERSION",
    "OIV_MODES", "SELF_CONTAINED_WARNING", "EnhancedOivTargetProfile",
    "JsonOivIdentityStore", "LegacyOivTargetProfile", "OivAssemblyWriter",
    "OivContentFile", "OivContentPlan", "OivContentPlanner", "OivExportRequest",
    "OivExportResult", "OivIdentityStore", "OivPackageBuilder",
    "OivPackageMetadata", "OivPackageValidator", "OivPackageVerifier",
    "OivTargetProfile", "StagedAxleConfiguration", "StagedRuntime",
    "StagedVehicleDlc",
]
