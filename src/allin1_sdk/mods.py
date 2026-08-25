"""Manifest-driven installation for optional, user-supplied GTA V mods."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import sys
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Iterator

from allin1_sdk.extensions import ExtensionManifest, ExtensionRegistry
from allin1_sdk.processes import run_hidden
from allin1_sdk.mod_package_contract import validate_mod_schema_envelope
from allin1_sdk.official_vehicle_models import OFFICIAL_VEHICLE_MODELS
from allin1_sdk.vehicle_catalog import VehicleCatalog

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

SUPPORTED_MOD_TYPES = frozenset({"asi", "script", "rpf", "config", "mixed"})
SUPPORTED_EDITIONS = frozenset({"legacy", "enhanced"})
SUPPORTED_DEPENDENCIES = frozenset({"scripthookv", "shvdn", "openrpf"})
_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_DLC_PACK_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_REQUIREMENT_PATTERN = re.compile(
    r"^([a-z0-9][a-z0-9._-]{1,63})(?:(==|>=)([0-9]+(?:\.[0-9]+){0,3}))?$"
)
_RESERVED_DESTINATIONS = frozenset({
    "dinput8.dll",
    "openiv.asi",
    "openrpf.asi",
    "scripthookv.dll",
    "scripthookvdotnet.asi",
    "scripthookvdotnet.ini",
    "scripthookvdotnet2.dll",
    "scripthookvdotnet3.dll",
    "scripts/allin1.dll",
    "scripts/allin1.toml",
})
MAX_PACKAGE_ARCHIVE_MEMBERS = 4096
MAX_PACKAGE_ARCHIVE_MEMBER_BYTES = 4 * 1024 * 1024 * 1024
MAX_PACKAGE_ARCHIVE_BYTES = 8 * 1024 * 1024 * 1024
MAX_PACKAGE_COMPRESSION_RATIO = 1000
_WINDOWS_INVALID_PATH_CHARS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_STEMS = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
})


def _relative_path(value: object, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty relative path")
    normalized = value.strip().replace("\\", "/")
    raw_parts = normalized.split("/")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError(f"{label} must not be absolute or contain traversal segments")
    for part in raw_parts:
        if (
            any(character in _WINDOWS_INVALID_PATH_CHARS for character in part)
            or any(ord(character) < 32 for character in part)
            or part.endswith((" ", "."))
            or part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_STEMS
        ):
            raise ValueError(
                f"{label} contains a Windows-invalid or reserved path component"
            )
    return path


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be an array of strings")
    return tuple(dict.fromkeys(item.strip().lower() for item in value if item.strip()))


def _dlc_pack_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("dlc_packs must be an array of pack-name strings")
    packs: list[str] = []
    seen: set[str] = set()
    for item in value:
        pack = item.strip()
        if not _DLC_PACK_PATTERN.fullmatch(pack):
            raise ValueError(
                "DLC pack names may contain only letters, numbers, dashes, and underscores"
            )
        key = pack.casefold()
        if key not in seen:
            seen.add(key)
            packs.append(pack)
    return tuple(packs)


def _contained_path(root: Path, relative: str | PurePosixPath) -> Path:
    """Return a lexical contained path after rejecting reparse aliases."""
    base = root.resolve()
    safe_relative = _relative_path(str(relative), "managed path")
    candidate = base / Path(*safe_relative.parts)
    canonical = candidate.resolve(strict=False)
    if not canonical.is_relative_to(base):
        raise ValueError(f"Path escapes the allowed root: {relative}")
    current = base
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for part in safe_relative.parts:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            continue
        if current.is_symlink() or (
            getattr(metadata, "st_file_attributes", 0) & reparse_flag
        ):
            raise ValueError(
                f"Managed paths may not traverse a symlink or junction: {relative}"
            )
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ModFile:
    source: PurePosixPath
    destination: PurePosixPath
    sha256: str | None = None


@dataclass(frozen=True)
class RpfEntryPatch:
    source: PurePosixPath
    archive: PurePosixPath
    entry: PurePosixPath
    sha256: str | None = None


@dataclass(frozen=True)
class PackageRequirement:
    """A dependency on another enabled ALLIN1-managed content package."""

    mod_id: str
    operator: str | None = None
    version: str | None = None

    @classmethod
    def parse(cls, value: str) -> "PackageRequirement":
        normalized = value.strip().lower().replace(" ", "")
        match = _REQUIREMENT_PATTERN.fullmatch(normalized)
        if not match:
            raise ValueError(
                "ALLIN1 package requirements use 'package.id', "
                "'package.id>=1.2', or 'package.id==1.2.3'"
            )
        return cls(match.group(1), match.group(2), match.group(3))

    def __str__(self) -> str:
        return self.mod_id + (
            f"{self.operator}{self.version}" if self.operator and self.version else ""
        )

    @staticmethod
    def _version_parts(value: str) -> tuple[int, ...]:
        return tuple(int(part) for part in value.split("."))

    def accepts(self, installed_version: str) -> bool:
        if self.operator is None or self.version is None:
            return True
        try:
            installed = self._version_parts(installed_version)
            required = self._version_parts(self.version)
        except ValueError:
            return False
        width = max(len(installed), len(required))
        installed += (0,) * (width - len(installed))
        required += (0,) * (width - len(required))
        if self.operator == "==":
            return installed == required
        return installed >= required

    def guarantees_minimum(self, minimum_version: str) -> bool:
        """Whether this declaration guarantees at least ``minimum_version``."""
        if self.operator != ">=" or self.version is None:
            return False
        try:
            declared = self._version_parts(self.version)
            minimum = self._version_parts(minimum_version)
        except ValueError:
            return False
        width = max(len(declared), len(minimum))
        declared += (0,) * (width - len(declared))
        minimum += (0,) * (width - len(minimum))
        return declared >= minimum


@dataclass(frozen=True)
class ExtensionReference:
    """Schema-v2 package envelope plus its fully validated content descriptor."""

    descriptor: ExtensionManifest
    requirements: tuple[str, ...]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.descriptor, name)

    def to_dict(self) -> dict[str, Any]:
        return self.descriptor.to_dict()


@dataclass(frozen=True)
class ModManifest:
    """A validated local mod package manifest."""

    manifest_path: Path
    mod_id: str
    name: str
    version: str
    mod_type: str
    description: str
    editions: tuple[str, ...]
    dependencies: tuple[str, ...]
    conflicts: tuple[str, ...]
    dlc_packs: tuple[str, ...]
    files: tuple[ModFile, ...]
    rpf_entries: tuple[RpfEntryPatch, ...]
    package_requirements: tuple[PackageRequirement, ...] = ()
    extension: ExtensionReference | None = None
    schema_version: int = 1

    @property
    def package_root(self) -> Path:
        return self.manifest_path.parent

    @classmethod
    def load(
        cls,
        manifest_path: str | Path,
        *,
        validate_payload: bool = True,
        reserved_models: Iterable[str] | None = None,
    ) -> "ModManifest":
        path = Path(manifest_path).resolve()
        if path.is_dir():
            path = path / "mod.toml"
        if not path.is_file():
            raise FileNotFoundError(f"Mod manifest not found: {path}")
        if path.name.casefold() != "mod.toml":
            raise ValueError(
                "Select the package's mod.toml manifest. Archives must first be "
                "inspected and linked in the Add-on Content SDK."
            )
        try:
            with path.open("rb") as stream:
                data = tomllib.load(stream)
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise ValueError(f"Invalid mod.toml manifest: {exc}") from exc

        schema_version, raw_allin1 = validate_mod_schema_envelope(data)
        mod_id = str(data.get("id", "")).strip().lower()
        if not _ID_PATTERN.fullmatch(mod_id):
            raise ValueError("Mod id must be 2-64 lowercase letters, numbers, dots, dashes, or underscores")
        if mod_id.startswith("allin1."):
            raise ValueError(
                "The allin1.* package namespace is reserved for launcher-bundled content"
            )
        name = str(data.get("name", "")).strip()
        version = str(data.get("version", "")).strip()
        mod_type = str(data.get("type", "")).strip().lower()
        if not name or not version:
            raise ValueError("Mod name and version are required")
        if mod_type not in SUPPORTED_MOD_TYPES:
            raise ValueError(f"Unsupported mod type '{mod_type}'")

        editions = _string_list(
            data.get("editions", ["legacy", "enhanced"]), "editions"
        )
        if not editions or not set(editions).issubset(SUPPORTED_EDITIONS):
            raise ValueError("editions may contain only 'legacy' and 'enhanced'")
        dependencies = _string_list(data.get("dependencies"), "dependencies")
        unknown_dependencies = set(dependencies) - SUPPORTED_DEPENDENCIES
        if unknown_dependencies:
            raise ValueError(f"Unsupported dependencies: {', '.join(sorted(unknown_dependencies))}")
        conflicts = _string_list(data.get("conflicts"), "conflicts")
        if mod_id in conflicts:
            raise ValueError("A mod package may not conflict with itself")
        dlc_packs = _dlc_pack_list(data.get("dlc_packs"))
        if dlc_packs and mod_type not in {"rpf", "mixed"}:
            raise ValueError("dlc_packs may be declared only by an RPF or mixed package")
        if dlc_packs and "openrpf" not in dependencies:
            raise ValueError("A package declaring dlc_packs must depend on openrpf")

        raw_files = data.get("files", [])
        raw_rpf_entries = data.get("rpf_entries", [])
        if not isinstance(raw_files, list) or not isinstance(raw_rpf_entries, list):
            raise ValueError("files and rpf_entries must be arrays of tables")
        if not raw_files and not raw_rpf_entries:
            raise ValueError(
                "A mod package must contain at least one [[files]] or "
                "[[rpf_entries]] entry"
            )
        files: list[ModFile] = []
        destinations: set[str] = set()
        for index, raw_file in enumerate(raw_files, start=1):
            if not isinstance(raw_file, dict):
                raise ValueError(f"files entry {index} must be a table")
            source = _relative_path(raw_file.get("source"), f"files[{index}].source")
            destination = _relative_path(
                raw_file.get("destination"), f"files[{index}].destination"
            )
            destination_key = destination.as_posix().lower()
            if (destination_key in _RESERVED_DESTINATIONS
                    or destination_key.startswith("scripts/.allin1/")):
                raise ValueError(f"Destination is reserved by the ALLIN1 launcher: {destination}")
            if destination_key in destinations:
                raise ValueError(f"Duplicate destination: {destination}")
            destinations.add(destination_key)
            checksum = raw_file.get("sha256")
            if checksum is not None:
                checksum = str(checksum).strip().lower()
                if not _SHA256_PATTERN.fullmatch(checksum):
                    raise ValueError(f"Invalid SHA-256 for {source}")
            files.append(ModFile(source, destination, checksum))

        rpf_entries: list[RpfEntryPatch] = []
        rpf_destinations: set[tuple[str, str]] = set()
        for index, raw_entry in enumerate(raw_rpf_entries, start=1):
            if not isinstance(raw_entry, dict):
                raise ValueError(f"rpf_entries entry {index} must be a table")
            source = _relative_path(
                raw_entry.get("source"), f"rpf_entries[{index}].source"
            )
            archive = _relative_path(
                raw_entry.get("archive"), f"rpf_entries[{index}].archive"
            )
            entry = _relative_path(
                raw_entry.get("entry"), f"rpf_entries[{index}].entry"
            )
            archive_key = archive.as_posix().casefold()
            if not archive_key.startswith("mods/") or archive.suffix.casefold() != ".rpf":
                raise ValueError(
                    "RPF entry archives must be .rpf paths below the GTA V mods directory"
                )
            key = (archive_key, entry.as_posix().casefold())
            if key in rpf_destinations:
                raise ValueError(f"Duplicate RPF entry destination: {archive}/{entry}")
            rpf_destinations.add(key)
            checksum = raw_entry.get("sha256")
            if checksum is not None:
                checksum = str(checksum).strip().lower()
                if not _SHA256_PATTERN.fullmatch(checksum):
                    raise ValueError(f"Invalid SHA-256 for {source}")
            rpf_entries.append(RpfEntryPatch(source, archive, entry, checksum))

        if rpf_entries and mod_type not in {"rpf", "mixed"}:
            raise ValueError("RPF entry patches require an RPF or mixed package")
        if rpf_entries and "openrpf" not in dependencies:
            raise ValueError("RPF entry patches require the openrpf dependency")

        extension: ExtensionReference | None = None
        package_requirements: tuple[PackageRequirement, ...] = ()
        if raw_allin1 is not None:
            content_path = _relative_path(
                raw_allin1.get("content"), "[allin1].content"
            )
            descriptor = ExtensionManifest.load(
                _contained_path(path.parent, content_path)
            )
            if descriptor.extension_id != mod_id:
                raise ValueError("Content manifest id must match the mod.toml id")
            if descriptor.version != version:
                raise ValueError("Content manifest version must match the mod.toml version")
            if any(setting.config_key for setting in descriptor.settings):
                raise ValueError(
                    "Packaged content settings may not bind launcher core config fields; "
                    "use package-namespaced settings"
                )
            raw_requires = _string_list(
                raw_allin1.get("requires"), "[allin1].requires"
            )
            package_requirements = tuple(
                PackageRequirement.parse(requirement) for requirement in raw_requires
            )
            requirement_ids = [requirement.mod_id for requirement in package_requirements]
            if len(requirement_ids) != len(set(requirement_ids)):
                raise ValueError("[allin1].requires contains duplicate package ids")
            if mod_id in requirement_ids:
                raise ValueError("A content package may not depend on itself")
            extension = ExtensionReference(descriptor, raw_requires)

        cls._validate_destinations(mod_type, files)
        if dlc_packs:
            actual_destinations = {
                item.destination.as_posix().casefold() for item in files
            }
            for pack in dlc_packs:
                expected = (
                    f"mods/update/x64/dlcpacks/{pack}/dlc.rpf".casefold()
                )
                if expected not in actual_destinations:
                    raise ValueError(
                        f"DLC pack '{pack}' must own exactly this payload destination: "
                        f"mods/update/x64/dlcpacks/{pack}/dlc.rpf"
                    )
        if extension is not None:
            extension.validate_package_destinations(
                item.destination.as_posix() for item in files
            )
            files_by_destination = {
                item.destination.as_posix().casefold(): item for item in files
            }
            for catalog in extension.gbay_catalogs:
                if catalog.kind != "vehicle":
                    continue
                owned_file = files_by_destination[catalog.source.as_posix().casefold()]
                vehicle_catalog = VehicleCatalog.load(
                    _contained_path(path.parent, owned_file.source)
                )
                if vehicle_catalog.catalog_id != catalog.catalog_id:
                    raise ValueError(
                        "Vehicle catalog id must match its GBAY catalog declaration: "
                        f"{catalog.catalog_id}"
                    )
                vehicle_catalog.validate_package_ownership(
                    dlc_packs,
                    allow_traffic="traffic.catalog" in extension.capabilities,
                    reserved_models=(
                        OFFICIAL_VEHICLE_MODELS
                        if reserved_models is None
                        else reserved_models
                    ),
                )
                if any(item.traffic.enabled for item in vehicle_catalog.vehicles):
                    try:
                        traffic_setting = extension.setting("traffic_enabled")
                    except KeyError as exc:
                        raise ValueError(
                            "Traffic-enabled vehicle catalogs require a package-namespaced "
                            "traffic_enabled setting"
                        ) from exc
                    if (
                        traffic_setting.setting_type != "boolean"
                        or traffic_setting.default is not False
                    ):
                        raise ValueError(
                            "Vehicle catalog traffic_enabled must be a boolean setting "
                            "that defaults to false"
                        )
            if any(catalog.kind == "vehicle" for catalog in extension.gbay_catalogs):
                online_requirement = next((
                    item for item in package_requirements
                    if item.mod_id == "allin1.online-content"
                ), None)
                if (
                    online_requirement is None
                    or not online_requirement.guarantees_minimum("0.5.5")
                ):
                    raise ValueError(
                        "GBAY vehicle catalogs require allin1.online-content>=0.5.5"
                    )
        manifest = cls(
            path,
            mod_id,
            name,
            version,
            mod_type,
            str(data.get("description", "")).strip(),
            editions,
            dependencies,
            conflicts,
            dlc_packs,
            tuple(files),
            tuple(rpf_entries),
            package_requirements,
            extension,
            schema_version,
        )
        if validate_payload:
            manifest.validate_payload()
        return manifest

    @staticmethod
    def _validate_destinations(mod_type: str, files: Iterable[ModFile]) -> None:
        for item in files:
            parts = tuple(part.lower() for part in item.destination.parts)
            suffix = item.destination.suffix.lower()
            if mod_type == "asi" and len(parts) != 1:
                raise ValueError("ASI mod files must install in the GTA V root")
            if mod_type == "asi" and suffix not in {".asi", ".dll", ".ini", ".toml"}:
                raise ValueError("ASI packages may contain only .asi, .dll, .ini, or .toml files")
            if mod_type == "script" and (not parts or parts[0] != "scripts"):
                raise ValueError("Script mod destinations must be below scripts/")
            if mod_type == "rpf" and (not parts or parts[0] != "mods"):
                raise ValueError("RPF mod destinations must be below mods/")
            if mod_type == "rpf" and suffix != ".rpf":
                raise ValueError("RPF packages may install only .rpf files")
            if mod_type == "config" and (not parts or parts[0] not in {"scripts", "mods"}):
                raise ValueError("Config/data mod destinations must be below scripts/ or mods/")
            if mod_type == "mixed":
                root_plugin = len(parts) == 1 and suffix in {
                    ".asi", ".dll", ".ini", ".toml", ".addon64",
                }
                managed_tree = bool(parts) and parts[0] in {
                    "scripts", "mods", "reshade-shaders",
                }
                if not root_plugin and not managed_tree:
                    raise ValueError(
                        "Mixed package files must target a supported root plug-in "
                        "or scripts/mods/reshade-shaders directory"
                    )

    def validate_payload(self) -> None:
        package_root = self.package_root.resolve()
        for item in self.files:
            unresolved_source = package_root / Path(*item.source.parts)
            if unresolved_source.is_symlink():
                raise ValueError(f"Package payload may not use symbolic links: {item.source}")
            source = _contained_path(package_root, item.source)
            if not source.is_file():
                raise FileNotFoundError(f"Package payload is missing: {item.source}")
            if item.sha256 and _sha256(source) != item.sha256:
                raise ValueError(f"SHA-256 mismatch for {item.source}")
        for item in self.rpf_entries:
            unresolved_source = package_root / Path(*item.source.parts)
            if unresolved_source.is_symlink():
                raise ValueError(f"Package payload may not use symbolic links: {item.source}")
            source = _contained_path(package_root, item.source)
            if not source.is_file():
                raise FileNotFoundError(f"Package payload is missing: {item.source}")
            if item.sha256 and _sha256(source) != item.sha256:
                raise ValueError(f"SHA-256 mismatch for {item.source}")


def _archive_member_path(info: zipfile.ZipInfo) -> PurePosixPath | None:
    """Validate one ZIP member without trusting the host ZIP extractor."""
    if info.flag_bits & 0x1:
        raise ValueError(f"Encrypted ZIP members are not supported: {info.filename}")
    normalized = info.filename.replace("\\", "/")
    is_directory = info.is_dir() or normalized.endswith("/")
    normalized = normalized.rstrip("/") if is_directory else normalized
    if not normalized:
        return None
    relative = _relative_path(normalized, "ZIP member path")
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    file_kind = stat.S_IFMT(unix_mode)
    if stat.S_ISLNK(unix_mode) or file_kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise ValueError(
            f"ZIP members may not be links or special files: {info.filename}"
        )
    dos_attributes = info.external_attr & 0xFFFF
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if dos_attributes & reparse_flag:
        raise ValueError(f"ZIP members may not be reparse points: {info.filename}")
    return relative


@contextmanager
def open_mod_package(
    source: str | Path, *, validate_payload: bool = True,
) -> Iterator[ModManifest]:
    """Open a folder/manifest or safely stage one unambiguous ZIP package."""
    selected = Path(source).expanduser()
    if selected.is_dir() or (
        selected.is_file() and selected.name.casefold() == "mod.toml"
    ):
        yield ModManifest.load(selected, validate_payload=validate_payload)
        return
    if not selected.is_file() or selected.suffix.casefold() != ".zip":
        raise ValueError("Select a package folder, mod.toml, or .zip archive")
    metadata = selected.lstat()
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if selected.is_symlink() or (
        getattr(metadata, "st_file_attributes", 0) & reparse_flag
    ):
        raise ValueError("Package archives may not be symbolic links or reparse points")

    try:
        with zipfile.ZipFile(selected) as package:
            infos = package.infolist()
            if len(infos) > MAX_PACKAGE_ARCHIVE_MEMBERS:
                raise ValueError(
                    f"ZIP contains too many members (maximum {MAX_PACKAGE_ARCHIVE_MEMBERS})"
                )
            members: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
            seen: set[str] = set()
            candidates: list[PurePosixPath] = []
            for info in infos:
                relative = _archive_member_path(info)
                if relative is None:
                    continue
                key = relative.as_posix().casefold()
                if key in seen:
                    raise ValueError(f"ZIP contains a duplicate member path: {relative}")
                seen.add(key)
                members.append((info, relative))
                if not info.is_dir() and relative.name.casefold() == "mod.toml":
                    candidates.append(relative)
            if not candidates:
                raise ValueError("ZIP package does not contain a mod.toml manifest")
            if len(candidates) != 1:
                names = ", ".join(path.as_posix() for path in candidates)
                raise ValueError(
                    "ZIP package contains multiple mod.toml manifests; "
                    f"select an unambiguous package archive ({names})"
                )

            manifest_member = candidates[0]
            package_prefix = manifest_member.parent
            extracted = [
                (info, relative) for info, relative in members
                if package_prefix == PurePosixPath(".")
                or relative == package_prefix
                or package_prefix in relative.parents
            ]
            declared_total = 0
            for info, relative in extracted:
                if info.is_dir():
                    continue
                if info.file_size < 0 or info.file_size > MAX_PACKAGE_ARCHIVE_MEMBER_BYTES:
                    raise ValueError(f"ZIP member is too large: {relative}")
                declared_total += info.file_size
                if declared_total > MAX_PACKAGE_ARCHIVE_BYTES:
                    raise ValueError("ZIP package expands beyond the allowed size limit")
                if info.file_size and (
                    info.compress_size == 0
                    or info.file_size / info.compress_size
                    > MAX_PACKAGE_COMPRESSION_RATIO
                ):
                    raise ValueError(f"ZIP member has an unsafe compression ratio: {relative}")

            with tempfile.TemporaryDirectory(prefix="allin1-mod-") as temporary:
                staging_root = Path(temporary).resolve()
                observed_total = 0
                for info, relative in extracted:
                    target = _contained_path(staging_root, relative)
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    member_total = 0
                    with package.open(info, "r") as input_stream, target.open("xb") as output:
                        for chunk in iter(lambda: input_stream.read(1024 * 1024), b""):
                            member_total += len(chunk)
                            observed_total += len(chunk)
                            if member_total > min(
                                info.file_size, MAX_PACKAGE_ARCHIVE_MEMBER_BYTES
                            ) or observed_total > MAX_PACKAGE_ARCHIVE_BYTES:
                                raise ValueError("ZIP package exceeded its declared size limit")
                            output.write(chunk)
                    if member_total != info.file_size:
                        raise ValueError(f"ZIP member size changed while reading: {relative}")
                yield ModManifest.load(
                    staging_root / Path(*manifest_member.parts),
                    validate_payload=validate_payload,
                )
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid ZIP package: {exc}") from exc


@dataclass(frozen=True)
class ModStatus:
    mod_id: str
    name: str
    version: str
    mod_type: str
    installed: bool
    enabled: bool


class ModCatalog:
    """Discovers optional packages checked into or copied beside the launcher."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def discover(self) -> list[ModManifest]:
        if not self.root.is_dir():
            return []
        manifests: list[ModManifest] = []
        for path in sorted(self.root.glob("*/mod.toml"), key=lambda value: str(value).lower()):
            # Defer payload existence and checksum work until installation. RPF
            # archives can be large enough that hashing them would freeze refresh.
            manifests.append(ModManifest.load(path, validate_payload=False))
        return manifests


class ModIntegrationService:
    """Installs optional mod packages with receipts, backups, and rollback."""

    def __init__(self, gta_path: str | Path) -> None:
        candidate = Path(gta_path).expanduser().resolve()
        if not candidate.is_dir() or not (
            (candidate / "GTA5.exe").is_file()
            or (candidate / "GTA5_Enhanced.exe").is_file()
            or (candidate / "PlayGTAV.exe").is_file()
            or (candidate / "update" / "update.rpf").is_file()
        ):
            raise ValueError(f"'{candidate}' does not appear to be a valid GTA V installation")
        self.gta_path = candidate
        self.state_root = self.gta_path / "scripts" / ".allin1" / "mods"
        self.backup_root = self.gta_path / "ALLIN1_Backups" / "Mods"

    @property
    def edition(self) -> str:
        return "enhanced" if (self.gta_path / "GTA5_Enhanced.exe").exists() else "legacy"

    def _receipt_path(self, mod_id: str) -> Path:
        if not _ID_PATTERN.fullmatch(mod_id):
            raise ValueError("Invalid mod id")
        return self.state_root / f"{mod_id}.json"

    def _read_receipt(self, mod_id: str) -> dict[str, Any]:
        path = self._receipt_path(mod_id)
        if not path.is_file():
            raise FileNotFoundError(f"Mod '{mod_id}' is not installed")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid install receipt for '{mod_id}'") from exc
        if data.get("id") != mod_id or not isinstance(data.get("files"), list):
            raise ValueError(f"Invalid install receipt for '{mod_id}'")
        return data

    def _write_receipt(self, receipt: dict[str, Any]) -> None:
        self.state_root.mkdir(parents=True, exist_ok=True)
        receipt_path = self._receipt_path(str(receipt["id"]))
        temporary = receipt_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        temporary.replace(receipt_path)

    def _set_dlc_registration(self, pack: str, enabled: bool) -> bool:
        if not _DLC_PACK_PATTERN.fullmatch(pack):
            raise ValueError(f"Invalid DLC pack name in install receipt: {pack}")
        patcher = (
            Path(__file__).resolve().parents[2]
            / "tools" / "RpfPatcher" / "RpfPatcher.exe"
        )
        if not patcher.is_file():
            raise FileNotFoundError(
                "RpfPatcher.exe is required for managed DLC registration; "
                "run runtools.ps1 to build the helper."
            )
        command = "register-dlc" if enabled else "unregister-dlc"
        result = run_hidden(
            [patcher, command, self.gta_path, pack],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or "unknown helper error").strip()
            raise RuntimeError(
                f"Could not {'register' if enabled else 'unregister'} DLC pack "
                f"'{pack}': {detail}"
            )
        return "No changes needed" not in result.stdout

    def _rpf_patcher_path(self) -> Path:
        patcher = (
            Path(__file__).resolve().parents[2]
            / "tools" / "RpfPatcher" / "RpfPatcher.exe"
        )
        if not patcher.is_file():
            raise FileNotFoundError(
                "RpfPatcher.exe is required for managed RPF entries; "
                "run runtools.ps1 to build the helper."
            )
        return patcher

    def _run_rpf_command(self, command: str, *arguments: object):
        result = run_hidden(
            [self._rpf_patcher_path(), command, self.gta_path, *arguments],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        return result

    def _ensure_mods_archive(self, relative: str | PurePosixPath) -> Path:
        archive_relative = PurePosixPath(relative)
        if not archive_relative.parts or archive_relative.parts[0].casefold() != "mods":
            raise ValueError(f"Managed RPF archive must be below mods/: {relative}")
        archive = _contained_path(self.gta_path, archive_relative)
        if archive.is_file():
            return archive
        if archive.exists():
            raise ValueError(f"RPF archive destination is not a file: {relative}")
        source_relative = PurePosixPath(*archive_relative.parts[1:])
        source = _contained_path(self.gta_path, source_relative)
        if not source.is_file():
            raise FileNotFoundError(
                f"Base archive for managed RPF patch is missing: {source_relative}"
            )
        archive.parent.mkdir(parents=True, exist_ok=True)
        temporary = archive.with_name(f".{archive.name}.allin1-copy")
        shutil.copy2(source, temporary)
        temporary.replace(archive)
        return archive

    def _extract_rpf_entry(
        self, archive: Path, entry: str | PurePosixPath, output: Path,
        *, allow_missing: bool = False,
    ) -> bool:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.unlink(missing_ok=True)
        result = self._run_rpf_command(
            "extract-entry", archive, PurePosixPath(entry).as_posix(), output,
        )
        if result.returncode == 0:
            if not output.is_file():
                raise RuntimeError("RPF helper reported success without extracting a file")
            return True
        detail = (result.stderr or result.stdout or "unknown helper error").strip()
        if allow_missing and result.returncode == 5 and "not found" in detail.casefold():
            return False
        raise RuntimeError(f"Could not extract RPF entry '{entry}': {detail}")

    def _replace_rpf_entry(
        self, archive: Path, entry: str | PurePosixPath, payload: Path,
    ) -> None:
        result = self._run_rpf_command(
            "replace-entry", archive, PurePosixPath(entry).as_posix(), payload,
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or "unknown helper error").strip()
            raise RuntimeError(f"Could not replace RPF entry '{entry}': {detail}")

    def _delete_rpf_entry(self, archive: Path, entry: str | PurePosixPath) -> None:
        result = self._run_rpf_command(
            "delete-entry", archive, PurePosixPath(entry).as_posix(),
        )
        if result.returncode:
            detail = (result.stderr or result.stdout or "unknown helper error").strip()
            raise RuntimeError(f"Could not delete RPF entry '{entry}': {detail}")

    def _rpf_entry_matches(
        self, item: dict[str, Any], expected: Path | None,
    ) -> bool:
        archive = _contained_path(self.gta_path, item["archive"])
        probe = _contained_path(
            self.state_root,
            PurePosixPath(".probes")
            / str(item.get("owner", "managed"))
            / hashlib.sha256(
                f"{item['archive']}\0{item['entry']}".encode("utf-8")
            ).hexdigest(),
        )
        try:
            exists = self._extract_rpf_entry(
                archive, item["entry"], probe, allow_missing=True,
            )
            if expected is None:
                return not exists
            return exists and _sha256(probe) == _sha256(expected)
        finally:
            probe.unlink(missing_ok=True)

    def _restore_rpf_record(self, item: dict[str, Any]) -> None:
        archive = _contained_path(self.gta_path, item["archive"])
        backup_value = item.get("backup")
        if backup_value:
            backup = _contained_path(self.gta_path, backup_value)
            if not backup.is_file():
                raise FileNotFoundError(
                    f"Managed RPF entry backup is missing: {backup}"
                )
            self._replace_rpf_entry(archive, item["entry"], backup)
        else:
            self._delete_rpf_entry(archive, item["entry"])

    def _rollback_rpf_records(self, records: Iterable[dict[str, Any]]) -> None:
        for item in reversed(list(records)):
            self._restore_rpf_record(item)

    def list_installed(self) -> list[ModStatus]:
        if not self.state_root.is_dir():
            return []
        statuses: list[ModStatus] = []
        for receipt_path in sorted(self.state_root.glob("*.json")):
            try:
                receipt = self._read_receipt(receipt_path.stem)
            except (OSError, ValueError):
                continue
            statuses.append(ModStatus(
                receipt["id"],
                str(receipt.get("name", receipt["id"])),
                str(receipt.get("version", "unknown")),
                str(receipt.get("type", "unknown")),
                True,
                bool(receipt.get("enabled", True)),
            ))
        return statuses

    def inspect_receipt(self, mod_id: str) -> dict[str, Any]:
        """Return one validated receipt without exposing a mutable internal object."""
        package_id = mod_id.strip().casefold()
        receipt = self._read_receipt(package_id)
        return json.loads(json.dumps(receipt))

    def verify_ownership(self, mod_id: str) -> dict[str, Any]:
        """Verify receipt-owned files, backups, and RPF entries without mutation."""
        package_id = mod_id.strip().casefold()
        receipt = self._read_receipt(package_id)
        enabled = bool(receipt.get("enabled", True))
        checks: list[dict[str, Any]] = []
        issues: list[str] = []
        for item in receipt["files"]:
            destination = str(item["destination"])
            target = _contained_path(self.gta_path, destination)
            active = target if enabled else target.with_name(target.name + ".disabled")
            exists = active.is_file() and not active.is_symlink()
            expected_hash = str(item.get("sha256") or "")
            hash_matches = _sha256(active) == expected_hash if exists and expected_hash else None
            backup_value = item.get("backup")
            backup_present = (
                _contained_path(self.gta_path, str(backup_value)).is_file()
                if backup_value else None
            )
            if not exists:
                issues.append(f"Managed file is missing: {destination}")
            elif hash_matches is False:
                issues.append(f"Managed file was externally changed: {destination}")
            if backup_present is False:
                issues.append(f"Managed backup is missing: {backup_value}")
            checks.append({
                "kind": "file", "destination": destination, "exists": exists,
                "hash_recorded": bool(expected_hash), "hash_matches": hash_matches,
                "backup_present": backup_present,
            })
        for item in receipt.get("rpf_entries", []):
            expected_value = item.get("applied") if enabled else item.get("backup")
            expected = (
                _contained_path(self.gta_path, str(expected_value))
                if expected_value else None
            )
            try:
                matches = self._rpf_entry_matches(item, expected)
            except (OSError, RuntimeError, ValueError) as exc:
                matches = False
                issues.append(
                    f"Could not verify RPF entry {item.get('archive')}/{item.get('entry')}: {exc}"
                )
            if not matches and not any(
                f"{item.get('archive')}/{item.get('entry')}" in issue for issue in issues
            ):
                issues.append(
                    f"Managed RPF entry does not match its receipt: "
                    f"{item.get('archive')}/{item.get('entry')}"
                )
            checks.append({
                "kind": "rpf_entry", "archive": item.get("archive"),
                "entry": item.get("entry"), "matches_receipt": matches,
            })
        return {
            "package_id": package_id, "version": str(receipt.get("version", "")),
            "enabled": enabled, "healthy": not issues,
            "ownership_verified": not issues and all(
                check.get("hash_recorded", True) for check in checks
            ),
            "checks": checks, "issues": issues,
        }

    def _check_dependencies(self, manifest: ModManifest) -> None:
        checks = {
            "scripthookv": self.gta_path / "ScriptHookV.dll",
            "shvdn": self.gta_path / "ScriptHookVDotNet.asi",
        }
        missing = [dependency for dependency in manifest.dependencies
                   if dependency in checks and not checks[dependency].is_file()]
        if "openrpf" in manifest.dependencies:
            raise RuntimeError(
                "Package installation belongs to the ALLIN1 Launcher; the SDK only "
                "validates and authors manifests."
            )
        if missing:
            raise ValueError(f"Missing required loader(s): {', '.join(missing)}")
        self._check_package_requirements(manifest.package_requirements)

    def _check_package_requirements(
        self, requirements: Iterable[PackageRequirement],
    ) -> None:
        required = tuple(requirements)
        if not required:
            return
        installed: dict[str, tuple[str, bool]] = {}
        for status in self.list_installed():
            installed[status.mod_id] = (status.version, status.enabled)
        try:
            for entry in ExtensionRegistry(self.gta_path).installed():
                installed[str(entry["id"])] = (
                    str(entry.get("version", "0")), bool(entry.get("enabled", False))
                )
        except (OSError, ValueError, KeyError):
            pass
        missing: list[str] = []
        for requirement in required:
            candidate = installed.get(requirement.mod_id)
            if candidate is None or not candidate[1] or not requirement.accepts(candidate[0]):
                missing.append(str(requirement))
        if missing:
            raise ValueError(
                "Missing required ALLIN1 content package(s): " + ", ".join(missing)
            )

    def _check_dependents(
        self, mod_id: str, *, replacement_version: str | None = None,
    ) -> None:
        dependents: list[str] = []
        for status in self.list_installed():
            if status.mod_id == mod_id or not status.enabled:
                continue
            receipt = self._read_receipt(status.mod_id)
            requirements = tuple(
                PackageRequirement.parse(str(value))
                for value in receipt.get("requires", [])
            )
            for requirement in requirements:
                if requirement.mod_id != mod_id:
                    continue
                if (
                    replacement_version is None
                    or not requirement.accepts(replacement_version)
                ):
                    dependents.append(f"{status.mod_id} ({requirement})")
                break
        if dependents:
            action = (
                "is required by" if replacement_version is None else
                f"cannot be updated to {replacement_version}; that version does not satisfy"
            )
            raise ValueError(
                f"Content package '{mod_id}' {action}: "
                + ", ".join(sorted(dependents))
            )

    def _check_conflicts(self, manifest: ModManifest) -> None:
        installed_statuses = self.list_installed()
        installed = {status.mod_id for status in installed_statuses}
        conflicts = installed.intersection(manifest.conflicts)
        for status in installed_statuses:
            if status.mod_id == manifest.mod_id:
                continue
            try:
                receipt = self._read_receipt(status.mod_id)
            except (OSError, ValueError):
                continue
            if manifest.mod_id in receipt.get("conflicts", []):
                conflicts.add(status.mod_id)
        if conflicts:
            raise ValueError(f"Conflicts with installed mod(s): {', '.join(sorted(conflicts))}")

        owned_destinations: dict[str, str] = {}
        owned_rpf_entries: dict[tuple[str, str], str] = {}
        for status in installed_statuses:
            if status.mod_id == manifest.mod_id:
                continue
            receipt = self._read_receipt(status.mod_id)
            for item in receipt["files"]:
                owned_destinations[str(item["destination"]).lower()] = status.mod_id
            for item in receipt.get("rpf_entries", []):
                key = (
                    str(item["archive"]).casefold(),
                    str(item["entry"]).casefold(),
                )
                owned_rpf_entries[key] = status.mod_id
        collisions = {
            owned_destinations[item.destination.as_posix().lower()]
            for item in manifest.files
            if item.destination.as_posix().lower() in owned_destinations
        }
        if collisions:
            raise ValueError(f"File destination is owned by: {', '.join(sorted(collisions))}")
        rpf_collisions = {
            owned_rpf_entries[
                (item.archive.as_posix().casefold(), item.entry.as_posix().casefold())
            ]
            for item in manifest.rpf_entries
            if (
                item.archive.as_posix().casefold(), item.entry.as_posix().casefold()
            ) in owned_rpf_entries
        }
        if rpf_collisions:
            raise ValueError(
                "RPF entry destination is owned by: "
                + ", ".join(sorted(rpf_collisions))
            )

    def install(self, manifest: ModManifest) -> ModStatus:
        manifest.validate_payload()
        if self.edition not in manifest.editions:
            raise ValueError(f"{manifest.name} does not support GTA V {self.edition.title()}")
        self._check_dependencies(manifest)
        self._check_conflicts(manifest)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        backup_dir = self.backup_root / manifest.mod_id / timestamp
        previous_receipt: dict[str, Any] | None = None
        previous_payloads: list[tuple[Path, str]] = []
        records: list[dict[str, Any]] = []
        rpf_records: list[dict[str, Any]] = []
        registered_packs: list[str] = []
        install_enabled = True
        applied_root = self.state_root / ".payloads" / manifest.mod_id / timestamp
        try:
            if self._receipt_path(manifest.mod_id).exists():
                previous_receipt = self._read_receipt(manifest.mod_id)
                install_enabled = bool(previous_receipt.get("enabled", True))
                self._check_dependents(
                    manifest.mod_id, replacement_version=manifest.version,
                )
                if manifest.rpf_entries or previous_receipt.get("rpf_entries"):
                    raise ValueError(
                        "Updating a package that owns RPF entries requires uninstalling "
                        "the existing version first"
                    )
                snapshot_root = backup_dir / ".update-rollback"
                previous_enabled = bool(previous_receipt.get("enabled", True))
                for old_item in previous_receipt["files"]:
                    target = _contained_path(self.gta_path, old_item["destination"])
                    current = target if previous_enabled else target.with_name(
                        target.name + ".disabled"
                    )
                    if not current.is_file():
                        raise FileNotFoundError(f"Managed mod file is missing: {current}")
                    snapshot = _contained_path(snapshot_root, old_item["destination"])
                    snapshot.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(current, snapshot)
                    previous_payloads.append((snapshot, str(old_item["destination"])))
                self.uninstall(manifest.mod_id, check_dependents=False)

            for item in manifest.files:
                source = _contained_path(manifest.package_root, item.source)
                target = _contained_path(self.gta_path, item.destination)
                target.parent.mkdir(parents=True, exist_ok=True)
                backup: Path | None = None
                if target.exists():
                    if not target.is_file() or target.is_symlink():
                        raise ValueError(f"Refusing to replace non-file destination: {item.destination}")
                    backup = _contained_path(backup_dir, item.destination)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                temporary = target.with_name(f".{target.name}.allin1-install")
                shutil.copy2(source, temporary)
                temporary.replace(target)
                records.append({
                    "destination": item.destination.as_posix(),
                    "backup": str(backup.relative_to(self.gta_path)).replace("\\", "/")
                    if backup else None,
                    "backup_sha256": _sha256(backup) if backup else None,
                    "sha256": _sha256(target),
                })

            for index, item in enumerate(manifest.rpf_entries, start=1):
                source = _contained_path(manifest.package_root, item.source)
                archive = self._ensure_mods_archive(item.archive)
                backup = _contained_path(
                    backup_dir,
                    PurePosixPath(".rpf-entries") / str(index) / item.entry,
                )
                existed = self._extract_rpf_entry(
                    archive, item.entry, backup, allow_missing=True,
                )
                if not existed:
                    backup.unlink(missing_ok=True)
                applied = _contained_path(
                    applied_root,
                    PurePosixPath(str(index)) / item.source.name,
                )
                applied.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, applied)
                record = {
                    "owner": manifest.mod_id,
                    "archive": item.archive.as_posix(),
                    "entry": item.entry.as_posix(),
                    "backup": str(backup.relative_to(self.gta_path)).replace("\\", "/")
                    if existed else None,
                    "applied": str(applied.relative_to(self.gta_path)).replace("\\", "/"),
                    "sha256": _sha256(applied),
                }
                rpf_records.append(record)
                self._replace_rpf_entry(archive, item.entry, applied)
                if not self._rpf_entry_matches(record, applied):
                    raise RuntimeError(
                        f"RPF entry verification failed: {item.archive}/{item.entry}"
                    )

            if not install_enabled:
                for item in records:
                    target = _contained_path(self.gta_path, item["destination"])
                    disabled = target.with_name(target.name + ".disabled")
                    if disabled.exists():
                        raise FileExistsError(
                            f"Cannot preserve disabled package state; destination exists: {disabled}"
                        )
                    target.replace(disabled)

            if install_enabled:
                for pack in manifest.dlc_packs:
                    if self._set_dlc_registration(pack, True):
                        registered_packs.append(pack)

            receipt = {
                "schema_version": 2 if manifest.extension else 1,
                "id": manifest.mod_id,
                "name": manifest.name,
                "version": manifest.version,
                "type": manifest.mod_type,
                "enabled": install_enabled,
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "source_manifest": str(manifest.manifest_path),
                "dependencies": list(manifest.dependencies),
                "conflicts": list(manifest.conflicts),
                "dlc_packs": list(manifest.dlc_packs),
                "requires": [
                    str(requirement) for requirement in manifest.package_requirements
                ],
                "extension": manifest.extension.to_dict() if manifest.extension else None,
                "owned_dlc_packs": list(registered_packs),
                "files": records,
                "rpf_entries": rpf_records,
            }
            self._write_receipt(receipt)
            ExtensionRegistry(self.gta_path).rebuild()
        except Exception:
            for pack in reversed(registered_packs):
                try:
                    self._set_dlc_registration(pack, False)
                except Exception:
                    pass
            self._rollback_records(records)
            try:
                self._rollback_rpf_records(rpf_records)
            finally:
                if applied_root.is_dir():
                    shutil.rmtree(applied_root)
            if previous_receipt is not None:
                previous_enabled = bool(previous_receipt.get("enabled", True))
                for snapshot, destination in previous_payloads:
                    target = _contained_path(self.gta_path, destination)
                    disabled = target.with_name(target.name + ".disabled")
                    target.unlink(missing_ok=True)
                    disabled.unlink(missing_ok=True)
                    restored = target if previous_enabled else disabled
                    restored.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(snapshot, restored)
                self._write_receipt(previous_receipt)
                if previous_enabled:
                    for pack in previous_receipt.get(
                        "owned_dlc_packs", previous_receipt.get("dlc_packs", []),
                    ):
                        self._set_dlc_registration(str(pack), True)
                try:
                    ExtensionRegistry(self.gta_path).rebuild()
                except Exception:
                    pass
            else:
                self._receipt_path(manifest.mod_id).unlink(missing_ok=True)
                try:
                    ExtensionRegistry(self.gta_path).rebuild()
                except Exception:
                    pass
            raise

        return ModStatus(
            manifest.mod_id, manifest.name, manifest.version, manifest.mod_type,
            True, install_enabled,
        )

    def _rollback_records(self, records: Iterable[dict[str, Any]]) -> None:
        for item in reversed(list(records)):
            target = _contained_path(self.gta_path, item["destination"])
            disabled = target.with_name(target.name + ".disabled")
            target.unlink(missing_ok=True)
            disabled.unlink(missing_ok=True)
            if item.get("backup"):
                backup = _contained_path(self.gta_path, item["backup"])
                if backup.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, target)

    def set_enabled(self, mod_id: str, enabled: bool) -> ModStatus:
        receipt = self._read_receipt(mod_id)
        current = bool(receipt.get("enabled", True))
        if current == enabled:
            if enabled and receipt.get("extension") is not None:
                registry = ExtensionRegistry(self.gta_path).rebuild()
                entry = next(
                    (item for item in registry["extensions"] if item["id"] == mod_id),
                    None,
                )
                if entry is None or not entry.get("enabled", False):
                    reason = entry.get("blocked_reason") if entry else "receipt was rejected"
                    raise ValueError(
                        f"Content package '{mod_id}' cannot be enabled: {reason}"
                    )
            return ModStatus(
                mod_id, receipt["name"], receipt["version"], receipt["type"], True, enabled
            )

        requirements = tuple(
            PackageRequirement.parse(str(value))
            for value in receipt.get("requires", [])
        )
        if enabled:
            self._check_package_requirements(requirements)
        else:
            self._check_dependents(mod_id)

        original_receipt = json.loads(json.dumps(receipt))
        moves: list[tuple[Path, Path]] = []
        changed_rpf_entries: list[dict[str, Any]] = []
        dlc_packs = [
            str(pack) for pack in receipt.get(
                "owned_dlc_packs", receipt.get("dlc_packs", []),
            )
        ]
        changed_registrations: list[str] = []

        # Fail before touching registrations, RPF entries, or receipts when a
        # managed loose file no longer matches its ownership receipt.
        for item in receipt["files"]:
            target = _contained_path(self.gta_path, item["destination"])
            disabled = target.with_name(target.name + ".disabled")
            source, destination = (disabled, target) if enabled else (target, disabled)
            if not source.is_file():
                raise FileNotFoundError(f"Managed mod file is missing: {source}")
            expected_hash = item.get("sha256")
            if expected_hash and _sha256(source) != expected_hash:
                raise RuntimeError(f"Managed mod file was externally changed: {source}")
            if destination.exists():
                raise FileExistsError(
                    f"Cannot change mod state; destination exists: {destination}"
                )
        try:
            if not enabled:
                for pack in dlc_packs:
                    self._set_dlc_registration(pack, False)
                    changed_registrations.append(pack)
                for item in reversed(receipt.get("rpf_entries", [])):
                    applied = _contained_path(self.gta_path, item["applied"])
                    if not self._rpf_entry_matches(item, applied):
                        raise RuntimeError(
                            f"Managed RPF entry was externally changed: "
                            f"{item['archive']}/{item['entry']}"
                        )
                    self._restore_rpf_record(item)
                    changed_rpf_entries.append(item)
            for item in receipt["files"]:
                target = _contained_path(self.gta_path, item["destination"])
                disabled = target.with_name(target.name + ".disabled")
                source, destination = (disabled, target) if enabled else (target, disabled)
                if not source.is_file():
                    raise FileNotFoundError(f"Managed mod file is missing: {source}")
                if destination.exists():
                    raise FileExistsError(f"Cannot change mod state; destination exists: {destination}")
                source.replace(destination)
                moves.append((destination, source))
            if enabled:
                for item in receipt.get("rpf_entries", []):
                    backup_value = item.get("backup")
                    backup = (
                        _contained_path(self.gta_path, backup_value)
                        if backup_value else None
                    )
                    if not self._rpf_entry_matches(item, backup):
                        raise RuntimeError(
                            f"RPF entry changed while the mod was disabled: "
                            f"{item['archive']}/{item['entry']}"
                        )
                    applied = _contained_path(self.gta_path, item["applied"])
                    self._replace_rpf_entry(
                        _contained_path(self.gta_path, item["archive"]),
                        item["entry"], applied,
                    )
                    changed_rpf_entries.append(item)
                for pack in dlc_packs:
                    self._set_dlc_registration(pack, True)
                    changed_registrations.append(pack)
            receipt["enabled"] = enabled
            self._write_receipt(receipt)
            registry = ExtensionRegistry(self.gta_path).rebuild()
            if enabled and receipt.get("extension") is not None:
                entry = next(
                    (item for item in registry["extensions"] if item["id"] == mod_id),
                    None,
                )
                if entry is None or not entry.get("enabled", False):
                    reason = entry.get("blocked_reason") if entry else "receipt was rejected"
                    raise ValueError(
                        f"Content package '{mod_id}' cannot be enabled: {reason}"
                    )
        except Exception:
            for item in reversed(changed_rpf_entries):
                try:
                    if enabled:
                        self._restore_rpf_record(item)
                    else:
                        applied = _contained_path(self.gta_path, item["applied"])
                        self._replace_rpf_entry(
                            _contained_path(self.gta_path, item["archive"]),
                            item["entry"], applied,
                        )
                except Exception:
                    pass
            for destination, source in reversed(moves):
                if destination.exists() and not source.exists():
                    destination.replace(source)
            for pack in reversed(changed_registrations):
                try:
                    self._set_dlc_registration(pack, not enabled)
                except Exception:
                    pass
            self._write_receipt(original_receipt)
            try:
                ExtensionRegistry(self.gta_path).rebuild()
            except Exception:
                pass
            raise
        return ModStatus(
            mod_id, receipt["name"], receipt["version"], receipt["type"], True, enabled
        )

    def uninstall(self, mod_id: str, *, check_dependents: bool = True) -> None:
        receipt = self._read_receipt(mod_id)
        if check_dependents:
            self._check_dependents(mod_id)
        was_enabled = bool(receipt.get("enabled", True))
        receipt_path = self._receipt_path(mod_id)
        receipt_snapshot = receipt_path.read_bytes()
        backups: dict[str, Path | None] = {}

        # Validate both layers before changing registrations, archives, files,
        # or the receipt.  When a package is disabled, only the .disabled file
        # is package-owned.  A live destination is either the verified backup
        # layer or an unrelated file that must never be deleted.
        for item in receipt["files"]:
            target = _contained_path(self.gta_path, item["destination"])
            disabled = target.with_name(target.name + ".disabled")
            current = target if was_enabled else disabled
            if not current.is_file():
                raise FileNotFoundError(f"Managed mod file is missing: {current}")
            expected_hash = item.get("sha256")
            if expected_hash and _sha256(current) != expected_hash:
                raise RuntimeError(
                    f"Refusing to remove externally changed managed file: {current}"
                )

            backup: Path | None = None
            backup_value = item.get("backup")
            if backup_value:
                backup = _contained_path(self.gta_path, backup_value)
                if not backup.is_file():
                    raise FileNotFoundError(f"Managed mod backup is missing: {backup}")
                expected_backup = item.get("backup_sha256")
                if expected_backup and _sha256(backup) != expected_backup:
                    raise RuntimeError(
                        f"Managed mod backup was externally changed: {backup}"
                    )
            backups[str(item["destination"])] = backup

            if not was_enabled:
                if backup is None and (target.exists() or target.is_symlink()):
                    raise RuntimeError(
                        f"Unmanaged file appeared while the mod was disabled: {target}"
                    )
                if backup is not None and target.exists():
                    if not target.is_file():
                        raise RuntimeError(
                            f"Underlying path is not a regular file: {target}"
                        )
                    expected_underlying = item.get("backup_sha256")
                    matches = (
                        _sha256(target) == expected_underlying
                        if expected_underlying else _sha256(target) == _sha256(backup)
                    )
                    if not matches:
                        raise RuntimeError(
                            f"Underlying file changed while the mod was disabled: {target}"
                        )

        if was_enabled:
            # Preflight all entry ownership before changing registrations or
            # archive content. A refusal must leave the installed mod intact.
            for item in reversed(receipt.get("rpf_entries", [])):
                applied = _contained_path(self.gta_path, item["applied"])
                if not self._rpf_entry_matches(item, applied):
                    raise RuntimeError(
                        f"Refusing to overwrite externally changed RPF entry: "
                        f"{item['archive']}/{item['entry']}"
                    )

        packs = [
            str(pack) for pack in receipt.get(
                "owned_dlc_packs", receipt.get("dlc_packs", []),
            )
        ]
        changed_packs: list[str] = []
        changed_rpf: list[dict[str, Any]] = []
        staged_loose: list[tuple[dict[str, Any], Path, bool]] = []
        uninstall_stage = (
            self.state_root / ".uninstall-rollback" / uuid.uuid4().hex
        )

        try:
            if was_enabled:
                for pack in packs:
                    self._set_dlc_registration(pack, False)
                    changed_packs.append(pack)
                for item in reversed(receipt.get("rpf_entries", [])):
                    self._restore_rpf_record(item)
                    changed_rpf.append(item)

            for item in reversed(receipt["files"]):
                target = _contained_path(self.gta_path, item["destination"])
                disabled = target.with_name(target.name + ".disabled")
                managed = target if was_enabled else disabled
                underlying_existed = target.exists() if not was_enabled else False
                stage_name = hashlib.sha256(
                    str(item["destination"]).encode("utf-8")
                ).hexdigest()[:20] + ".payload"
                stage = _contained_path(uninstall_stage, stage_name)
                stage.parent.mkdir(parents=True, exist_ok=True)
                managed.replace(stage)
                staged_loose.append((item, stage, underlying_existed))

                backup = backups[str(item["destination"])]
                if backup is not None and not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_name(
                        f".{target.name}.allin1-{uuid.uuid4().hex}.tmp"
                    )
                    try:
                        shutil.copy2(backup, temporary)
                        temporary.replace(target)
                    finally:
                        temporary.unlink(missing_ok=True)

            receipt_path.unlink()
            ExtensionRegistry(self.gta_path).rebuild()
        except Exception:
            # Restore the loose-file layering first.  Keep any stage files that
            # cannot be restored so a rollback failure never destroys the sole
            # remaining copy of a managed payload.
            for item, stage, underlying_existed in reversed(staged_loose):
                try:
                    target = _contained_path(self.gta_path, item["destination"])
                    disabled = target.with_name(target.name + ".disabled")
                    destination = target if was_enabled else disabled
                    if was_enabled and item.get("backup"):
                        target.unlink(missing_ok=True)
                    elif (
                        not was_enabled
                        and item.get("backup")
                        and not underlying_existed
                    ):
                        target.unlink(missing_ok=True)
                    if stage.is_file() and not destination.exists():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        stage.replace(destination)
                except Exception:
                    pass
            for item in reversed(changed_rpf):
                try:
                    applied = _contained_path(self.gta_path, item["applied"])
                    self._replace_rpf_entry(
                        _contained_path(self.gta_path, item["archive"]),
                        item["entry"], applied,
                    )
                except Exception:
                    pass
            for pack in reversed(changed_packs):
                try:
                    self._set_dlc_registration(pack, True)
                except Exception:
                    pass
            try:
                if not receipt_path.exists():
                    receipt_path.parent.mkdir(parents=True, exist_ok=True)
                    temporary_receipt = receipt_path.with_name(
                        f".{receipt_path.name}.{uuid.uuid4().hex}.rollback"
                    )
                    try:
                        temporary_receipt.write_bytes(receipt_snapshot)
                        temporary_receipt.replace(receipt_path)
                    finally:
                        temporary_receipt.unlink(missing_ok=True)
                ExtensionRegistry(self.gta_path).rebuild()
            except Exception:
                pass
            raise

        shutil.rmtree(uninstall_stage, ignore_errors=True)
        payload_root = self.state_root / ".payloads" / mod_id
        if payload_root.is_dir():
            shutil.rmtree(payload_root, ignore_errors=True)
