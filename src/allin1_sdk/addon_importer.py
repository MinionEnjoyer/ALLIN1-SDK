"""Safe package inspection and draft SDK-manifest generation."""

from __future__ import annotations

import json
import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from allin1_sdk.processes import hidden_process_options
from allin1_sdk.mod_package_contract import (
    WeaponEnhancementContract,
    parse_workbench_contract,
)
from allin1_sdk.material_progression import (
    MaterialProgressionReport,
    audit_material_progressions,
)

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


MAX_PACKAGE_FILES = 10_000
MAX_PACKAGE_BYTES = 2 * 1024 * 1024 * 1024
MAX_XML_BYTES = 16 * 1024 * 1024
MAX_PREVIEW_BYTES = 8 * 1024 * 1024
MAX_BINARY_HEADER_BYTES = 1024 * 1024
MAX_FOLDER_READ_WORKERS = 8
MAX_RECURSIVE_RPF_MEMBERS = 64
MAX_DIRECT_RPF_BYTES = 4 * 1024 * 1024 * 1024
XML_SUFFIXES = frozenset({".xml", ".meta"})
EXTERNAL_ARCHIVE_SUFFIXES = frozenset({".rar", ".7z"})
MANIFEST_TEXT_SUFFIXES = frozenset({".lua"})
PARSED_TEXT_SUFFIXES = XML_SUFFIXES | MANIFEST_TEXT_SUFFIXES
INSPECTION_TEXT_SUFFIXES = PARSED_TEXT_SUFFIXES | frozenset({
    ".txt", ".md", ".toml", ".json",
})
BINARY_PLUGIN_SUFFIXES = frozenset({".dll", ".asi", ".addon64"})
TEXT_SUFFIXES = frozenset({
    ".xml", ".meta", ".json", ".toml", ".txt", ".ini", ".cfg",
    ".log", ".md", ".lua", ".cs", ".asi.xml",
})
IMAGE_SUFFIXES = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".dds",
})
ASSET_SUFFIXES = frozenset({
    ".ydr", ".ydd", ".yft", ".ytd", ".ybn", ".ymap", ".ytyp",
    ".ycd", ".yed", ".yfd", ".ymf", ".ymt", ".ynd", ".ynv",
    ".ypt", ".yvr", ".ywr", ".awc", ".rel", ".gfx", ".gxt2",
})


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _safe_member_path(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    path = PurePosixPath(normalized)
    if (not normalized or any(ord(char) < 32 for char in normalized)
            or normalized.startswith("/") or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or ":" in path.parts[0]):
        raise ValueError(f"Unsafe package member path: {value}")
    return path


def _external_archive_tool() -> str:
    """Return a libarchive-compatible reader for RAR/7z packages."""
    for name in ("bsdtar", "tar"):
        executable = shutil.which(name)
        if executable:
            return executable
    raise ValueError(
        "RAR/7z inspection requires bsdtar (included with current Windows builds)"
    )


def _run_archive_command(arguments: list[str], *, timeout: int = 60) -> bytes:
    command = [_external_archive_tool(), *arguments]
    completed = subprocess.run(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=timeout, check=False, **hidden_process_options(),
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(f"Archive inspection failed: {detail or 'unknown error'}")
    return completed.stdout


def _list_external_archive(archive: Path) -> list[tuple[str, int]]:
    """List regular files in a RAR/7z without extracting the package."""
    output = _run_archive_command(["-tvf", str(archive)], timeout=120)
    entries: list[tuple[str, int]] = []
    for raw_line in output.decode("utf-8", errors="replace").splitlines():
        parts = raw_line.split(None, 8)
        if len(parts) != 9:
            raise ValueError("Archive listing used an unsupported format")
        mode, size_text, name = parts[0], parts[4], parts[8]
        if mode.startswith("d") or name.endswith("/"):
            continue
        try:
            size = int(size_text)
        except ValueError as exc:
            raise ValueError("Archive listing contains an invalid file size") from exc
        entries.append((_safe_member_path(name).as_posix(), size))
    return entries


def _read_external_archive_member(
    archive: Path, member: str, *, limit: int,
) -> tuple[bytes, bool]:
    """Stream a bounded member from a RAR/7z and stop before unbounded buffering."""
    # libarchive's command-line matcher treats member names as shell-style
    # patterns even though no shell is involved. Escape pattern metacharacters
    # so a valid name such as ``ReadMe [ENG].txt`` is addressed literally and
    # cannot concatenate multiple wildcard matches into one preview.
    literal_member = member.replace("[", "[[]").replace("*", "[*]").replace(
        "?", "[?]"
    )
    command = [
        _external_archive_tool(), "-xOf", str(archive), "--", literal_member,
    ]
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        **hidden_process_options(),
    )
    assert process.stdout is not None
    try:
        data = process.stdout.read(limit + 1)
        truncated = len(data) > limit
        if truncated:
            data = data[:limit]
            process.terminate()
        try:
            _, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr = process.communicate()
    except Exception:
        process.kill()
        process.communicate()
        raise
    if not truncated and process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(
            f"Could not read archive member {member}: {detail or 'unknown error'}"
        )
    return data, truncated


def _parse_xml(content: bytes, path: str) -> ET.Element:
    """Parse bounded metadata while refusing XML entity/DTD expansion."""
    uppercase = content[:4096].upper()
    if b"<!DOCTYPE" in uppercase or b"<!ENTITY" in uppercase:
        raise ValueError(f"DTD/entity declarations are not allowed in {path}")
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        # A handful of shipped GTA metadata files declare UTF-16 while their
        # payload is actually UTF-8/ASCII.  Treat that narrowly defined header
        # mismatch as recoverable; genuine malformed XML still fails closed.
        declared_utf16 = re.search(
            br"encoding\s*=\s*(['\"])utf-16(?:le|be)?\1",
            content[:512],
            flags=re.IGNORECASE,
        )
        has_utf16_bytes = (
            content.startswith((b"\xff\xfe", b"\xfe\xff"))
            or b"\x00" in content[:512]
        )
        if not declared_utf16 or has_utf16_bytes:
            raise
        repaired = re.sub(
            br"encoding\s*=\s*(['\"])utf-16(?:le|be)?\1",
            br"encoding=\1UTF-8\1",
            content,
            count=1,
            flags=re.IGNORECASE,
        )
        return ET.fromstring(repaired)


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", value.lower()).strip("-._")
    return cleaned[:70] or "package"


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _unique_casefold(values: Iterable[str]) -> list[str]:
    """Keep the first spelling while comparing game identifiers by case."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        identifier = value.casefold()
        if identifier in seen:
            continue
        seen.add(identifier)
        result.append(value)
    return result


def _identifier_starts_with(value: str, prefix: str) -> bool:
    """Match game-facing hash names without changing their authored spelling."""
    return value.casefold().startswith(prefix.casefold())


def _inspection_size_finding(suffix: str, path: str) -> PackageFinding:
    if suffix in XML_SUFFIXES:
        return PackageFinding(
            "warning", "xml_too_large",
            "XML metadata exceeds the 16 MiB parser limit.", path,
        )
    return PackageFinding(
        "warning", "inspection_text_too_large",
        "Text content exceeds the 16 MiB inspection limit.", path,
    )


def _documentation_supports_edition(text: str, edition: str) -> bool:
    """Return true when at least one edition mention is not an exclusion.

    Documentation commonly retains old incompatibility notes in a changelog.
    Scope exclusions to the sentence containing each mention so an obsolete
    ``Legacy unsupported`` line cannot cancel a later ``Legacy support added``
    statement elsewhere in the same README.
    """
    aliases = (edition,) if edition != "legacy" else (
        "legacy", "classic version", "classic/legacy version",
    )
    fragments = re.split(r"(?:[.!?](?:\s+|$)|[\r\n]+)", text)
    for fragment in fragments:
        for alias in aliases:
            label = re.escape(alias)
            if not re.search(rf"\b{label}\b", fragment):
                continue
            unsupported = (
                rf"\b(?:does\s+not|doesn't|do\s+not|don't)\s+support\b"
                rf".{{0,40}}\b{label}\b",
                rf"\b(?:no|without)\s+{label}\s+(?:support|compatibility)\b",
                rf"\b{label}\b.{{0,40}}\b(?:not\s+supported|unsupported|"
                rf"incompatible|does\s+not\s+work|doesn't\s+work)\b",
                rf"\b(?:not\s+supported|unsupported|incompatible)\b"
                rf".{{0,40}}\b{label}\b",
            )
            if not any(re.search(pattern, fragment) for pattern in unsupported):
                return True
    return False


def _binary_plugin_record(path: str, content: bytes | None) -> BinaryPluginRecord:
    if not content or len(content) < 64 or content[:2] != b"MZ":
        return BinaryPluginRecord(path, "unknown", "unknown", False)
    pe_offset = int.from_bytes(content[0x3C:0x40], "little")
    if pe_offset + 6 > len(content) or content[pe_offset:pe_offset + 4] != b"PE\0\0":
        return BinaryPluginRecord(path, "invalid-pe", "unknown", False)
    machine = int.from_bytes(content[pe_offset + 4:pe_offset + 6], "little")
    section_count = int.from_bytes(content[pe_offset + 6:pe_offset + 8], "little")
    optional_size = int.from_bytes(content[pe_offset + 20:pe_offset + 22], "little")
    optional_offset = pe_offset + 24
    optional_magic = int.from_bytes(
        content[optional_offset:optional_offset + 2], "little"
    )
    directory_offset = optional_offset + (112 if optional_magic == 0x20B else 96)
    clr_directory = directory_offset + (14 * 8)
    clr_rva = int.from_bytes(content[clr_directory:clr_directory + 4], "little")
    managed = clr_rva != 0
    cor_flags: int | None = None
    if managed:
        section_offset = optional_offset + optional_size
        for index in range(section_count):
            header = section_offset + (index * 40)
            virtual_size = int.from_bytes(content[header + 8:header + 12], "little")
            virtual_address = int.from_bytes(
                content[header + 12:header + 16], "little"
            )
            raw_size = int.from_bytes(content[header + 16:header + 20], "little")
            raw_pointer = int.from_bytes(content[header + 20:header + 24], "little")
            span = max(virtual_size, raw_size)
            if virtual_address <= clr_rva < virtual_address + span:
                clr_offset = raw_pointer + (clr_rva - virtual_address)
                if clr_offset + 20 <= len(content):
                    cor_flags = int.from_bytes(
                        content[clr_offset + 16:clr_offset + 20], "little"
                    )
                break
    architectures = {
        0x014C: "x86", 0x8664: "x64", 0xAA64: "arm64",
    }
    architecture = architectures.get(machine, f"machine-0x{machine:04x}")
    if managed and machine == 0x014C:
        architecture = "x86" if cor_flags is not None and cor_flags & 0x2 else "anycpu"
    return BinaryPluginRecord(
        path, "pe", architecture, managed or b"BSJB" in content,
    )


def asset_category(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "Images"
    if suffix in XML_SUFFIXES or suffix in {".json", ".toml", ".ini", ".cfg"}:
        return "Metadata"
    if suffix in {
        ".ydr", ".ydd", ".yft", ".ybn", ".ymap", ".ytyp", ".ymt",
        ".ymf", ".ynd", ".ynv", ".ypt", ".ycd", ".yed", ".yfd",
        ".yvr", ".ywr",
    }:
        return "Models & world"
    if suffix in {".ytd", ".gfx", ".gxt2"}:
        return "Textures & UI"
    if suffix in {".awc", ".rel"}:
        return "Audio"
    if suffix == ".rpf":
        return "Archives"
    if suffix in {".dll", ".asi", ".cs", ".lua"}:
        return "Scripts"
    if suffix in TEXT_SUFFIXES:
        return "Text"
    return "Other"


def asset_preview_kind(path: str) -> str:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in TEXT_SUFFIXES:
        return "text"
    return "binary"


def package_member_path(path: str) -> PurePosixPath:
    """Return the logical member path for a package or recursive RPF entry id.

    RPF indexes use stable ids such as ``x64/vehicles.rpf::model.yft``.  Those
    ids are required when extracting the entry, but their archive prefix must
    not become part of filename/stem matching in the specialist workbenches.
    """
    return PurePosixPath(path.rsplit("::", 1)[-1])


def decode_text_preview(content: bytes) -> str:
    """Decode authored text without failing the viewer on legacy encodings."""
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="replace")


def hex_preview(content: bytes, *, width: int = 16, rows: int = 16) -> str:
    lines: list[str] = []
    for offset in range(0, min(len(content), width * rows), width):
        chunk = content[offset:offset + width]
        hexadecimal = " ".join(f"{value:02X}" for value in chunk)
        printable = "".join(chr(value) if 32 <= value < 127 else "." for value in chunk)
        lines.append(f"{offset:08X}  {hexadecimal:<{width * 3 - 1}}  {printable}")
    return "\n".join(lines)


@dataclass(frozen=True)
class PackageFinding:
    severity: str
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True)
class PackageEntry:
    path: str
    size: int
    content: bytes | None = None

    @property
    def suffix(self) -> str:
        return package_member_path(self.path).suffix.lower()

    @property
    def name(self) -> str:
        return package_member_path(self.path).name

    @property
    def stem(self) -> str:
        return package_member_path(self.path).stem

    @property
    def category(self) -> str:
        return asset_category(self.path)

    @property
    def preview_kind(self) -> str:
        return asset_preview_kind(self.path)


@dataclass(frozen=True)
class PackageAssetContent:
    path: str
    size: int
    data: bytes
    truncated: bool
    sha256: str | None
    preview_kind: str


class PackageAssetReader:
    """Read one bounded package member without extraction or path traversal."""

    def __init__(
        self, source: str | Path, *,
        project_root: str | Path | None = None,
        gta_path: str | Path | None = None,
    ) -> None:
        self.source = Path(source).expanduser().resolve()
        self._external_entries: dict[str, tuple[str, int]] | None = None
        self._rpf_service = None
        self._rpf_index = None
        if self.source.is_dir():
            self.source_kind = "folder"
        elif self.source.is_file() and self.source.suffix.lower() in {".oiv", ".zip"}:
            self.source_kind = "archive"
        elif (self.source.is_file()
              and self.source.suffix.lower() in EXTERNAL_ARCHIVE_SUFFIXES):
            self.source_kind = "external_archive"
            listed = _list_external_archive(self.source)
            entries: dict[str, tuple[str, int]] = {}
            for path, size in listed:
                key = path.casefold()
                if key in entries:
                    raise ValueError(f"Ambiguous package asset: {path}")
                entries[key] = (path, size)
            self._external_entries = entries
        elif self.source.is_file() and self.source.suffix.lower() == ".rpf":
            if project_root is None or gta_path is None:
                raise ValueError(
                    "Direct RPF access requires the SDK project and a matching "
                    "GTA V installation path"
                )
            from allin1_sdk.rpf_tools import RpfExplorerService

            self.source_kind = "rpf"
            self._rpf_service = RpfExplorerService(project_root, gta_path)
            self._rpf_index = self._rpf_service.index(self.source)
        else:
            raise ValueError(
                "Asset viewer requires a package folder, .rpf, or "
                ".oiv/.zip/.rar/.7z"
            )

    def read(
        self, entry_path: str, *, limit: int = MAX_PREVIEW_BYTES,
    ) -> PackageAssetContent:
        if limit <= 0:
            raise ValueError("Asset preview limit must be positive")
        if self.source_kind == "rpf":
            return self._read_rpf(entry_path, limit)
        relative = _safe_member_path(entry_path).as_posix()
        if self.source_kind == "folder":
            return self._read_folder(relative, limit)
        if self.source_kind == "external_archive":
            return self._read_external_archive(relative, limit)
        return self._read_archive(relative, limit)

    def _read_rpf(self, entry_path: str, limit: int) -> PackageAssetContent:
        """Read the direct archive itself or one exact recursively indexed entry."""
        if entry_path.casefold() == self.source.name.casefold():
            size = self.source.stat().st_size
            with self.source.open("rb") as stream:
                data = stream.read(min(size, limit))
            return self._content(entry_path, size, data, size > limit)
        if self._rpf_service is None or self._rpf_index is None:
            raise ValueError("Direct RPF reader was not initialized")
        try:
            entry = self._rpf_index.entry(entry_path)
        except KeyError as exc:
            raise FileNotFoundError(f"RPF asset not found: {entry_path}") from exc
        if entry.kind == "directory":
            raise ValueError(f"RPF directories cannot be read as assets: {entry_path}")
        with tempfile.TemporaryDirectory(prefix="allin1-package-rpf-read-") as temporary:
            destination = Path(temporary) / f"entry{entry.suffix or '.bin'}"
            extracted = self._rpf_service.extract(
                self._rpf_index, entry, destination,
            )
            size = extracted.stat().st_size
            with extracted.open("rb") as stream:
                data = stream.read(min(size, limit))
        return self._content(entry_path, size, data, size > limit)

    def _read_folder(self, relative: str, limit: int) -> PackageAssetContent:
        root = self.source.resolve()
        unresolved = root / Path(*PurePosixPath(relative).parts)
        candidate = unresolved.resolve(strict=False)
        if unresolved.is_symlink() or not candidate.is_relative_to(root):
            raise ValueError(f"Asset path escapes the package root: {relative}")
        if not candidate.is_file():
            raise FileNotFoundError(f"Package asset not found: {relative}")
        size = candidate.stat().st_size
        with candidate.open("rb") as stream:
            data = stream.read(min(size, limit))
        return self._content(relative, size, data, size > limit)

    def _read_archive(self, relative: str, limit: int) -> PackageAssetContent:
        try:
            with zipfile.ZipFile(self.source) as package:
                matches = [
                    member for member in package.infolist()
                    if not member.is_dir()
                    and _safe_member_path(member.filename).as_posix().casefold()
                    == relative.casefold()
                ]
                if len(matches) != 1:
                    if matches:
                        raise ValueError(f"Ambiguous package asset: {relative}")
                    raise FileNotFoundError(f"Package asset not found: {relative}")
                member = matches[0]
                if member.flag_bits & 0x1:
                    raise ValueError(f"Encrypted package asset cannot be viewed: {relative}")
                with package.open(member) as stream:
                    data = stream.read(min(member.file_size, limit))
                return self._content(
                    relative, member.file_size, data, member.file_size > limit,
                )
        except zipfile.BadZipFile as exc:
            raise ValueError(f"Invalid package archive: {exc}") from exc

    def _read_external_archive(
        self, relative: str, limit: int,
    ) -> PackageAssetContent:
        assert self._external_entries is not None
        match = self._external_entries.get(relative.casefold())
        if match is None:
            raise FileNotFoundError(f"Package asset not found: {relative}")
        authored_path, size = match
        data, truncated = _read_external_archive_member(
            self.source, authored_path, limit=min(size, limit),
        )
        if not truncated and len(data) != size:
            raise ValueError(
                f"Archive member size mismatch for {authored_path}: "
                f"expected {size}, read {len(data)}"
            )
        return self._content(authored_path, size, data, truncated or size > limit)

    @staticmethod
    def _content(
        relative: str, size: int, data: bytes, truncated: bool,
    ) -> PackageAssetContent:
        digest = None if truncated else hashlib.sha256(data).hexdigest()
        return PackageAssetContent(
            relative, size, data, truncated, digest, asset_preview_kind(relative),
        )


@dataclass(frozen=True)
class WeaponRecord:
    source: str
    name: str
    slot: str
    ammo_info: str
    model: str
    human_name_hash: str
    stat_name: str


@dataclass(frozen=True)
class WeaponComponentRecord:
    source: str
    name: str
    model: str
    loc_name: str
    loc_desc: str
    attach_bone: str
    component_type: str


@dataclass(frozen=True)
class WeaponComponentLink:
    source: str
    weapon_name: str
    component_name: str
    attach_bone: str
    default: bool


@dataclass(frozen=True)
class WeaponAnimationRecord:
    source: str
    weapon_name: str
    field_name: str
    representation: str
    set_name: str
    set_ordinal: int
    ordinal: int


@dataclass(frozen=True)
class WeaponShopRecord:
    source: str
    weapon_name: str
    field_name: str
    representation: str
    ordinal: int


@dataclass(frozen=True)
class AmmoRecord:
    source: str
    name: str
    model: str
    ammo_max: str
    ammo_max_50: str
    explosion: str
    trail_fx: str
    primed_fx: str


@dataclass(frozen=True)
class VehicleRecord:
    source: str
    model_name: str
    txd_name: str
    handling_id: str
    game_name: str
    make_name: str
    audio_name_hash: str
    layout: str
    vehicle_type: str
    vehicle_class: str
    edition: str = ""


@dataclass(frozen=True)
class HandlingRecord:
    source: str
    name: str
    edition: str = ""


@dataclass(frozen=True)
class VehicleVariationRecord:
    source: str
    model_name: str
    kits: tuple[str, ...]
    light_settings: str
    edition: str = ""


@dataclass(frozen=True)
class VehicleKitRecord:
    source: str
    name: str
    kit_id: str
    model_names: tuple[str, ...]
    edition: str = ""


@dataclass(frozen=True)
class PedRecord:
    source: str
    name: str
    ped_type: str
    model_type: str
    props_name: str
    clip_dictionary: str
    expression_set: str
    movement_clip_set: str
    creature_metadata: str


@dataclass(frozen=True)
class PackageRegistrationRecord:
    source: str
    kind: str
    package_names: tuple[str, ...]
    metadata_files: tuple[str, ...]


@dataclass(frozen=True)
class BinaryPluginRecord:
    path: str
    format: str
    architecture: str
    managed: bool


@dataclass(frozen=True)
class RpfPackageRecord:
    """One package-owned RPF that was recursively indexed read-only."""

    source: str
    edition: str
    archive_count: int
    entry_count: int
    suffix_counts: dict[str, int]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RpfNativeEntryRecord:
    """A native asset discovered at any depth inside a package-owned RPF."""

    source: str
    archive_path: str
    path: str
    entry_id: str
    kind: str
    suffix: str
    size: int


@dataclass(frozen=True)
class RpfPackageInspection:
    """Structured facts promoted from package-owned RPF archives."""

    archives: tuple[RpfPackageRecord, ...] = ()
    indexed_entries: tuple[PackageEntry, ...] = ()
    native_assets: tuple[RpfNativeEntryRecord, ...] = ()
    material_progressions: tuple[MaterialProgressionReport, ...] = ()
    vehicles: tuple[VehicleRecord, ...] = ()
    handlings: tuple[HandlingRecord, ...] = ()
    variations: tuple[VehicleVariationRecord, ...] = ()
    kits: tuple[VehicleKitRecord, ...] = ()
    registrations: tuple[PackageRegistrationRecord, ...] = ()
    weapons: tuple[WeaponRecord, ...] = ()
    ammo: tuple[AmmoRecord, ...] = ()
    weapon_components: tuple[WeaponComponentRecord, ...] = ()
    weapon_component_links: tuple[WeaponComponentLink, ...] = ()
    weapon_animation_records: tuple[WeaponAnimationRecord, ...] = ()
    weapon_shop_records: tuple[WeaponShopRecord, ...] = ()
    peds: tuple[PedRecord, ...] = ()


@dataclass(frozen=True)
class ScriptedWeaponSystemRecord:
    """A schema-2 runtime system that enhances vanilla weapon behavior."""

    system_id: str
    name: str
    capabilities: tuple[str, ...]
    script_entry_points: tuple[str, ...]
    relationships_declared: bool


@dataclass(frozen=True)
class PackageScan:
    source: Path
    source_kind: str
    entries: tuple[PackageEntry, ...]
    findings: tuple[PackageFinding, ...]
    weapons: tuple[WeaponRecord, ...]
    ammo: tuple[AmmoRecord, ...]
    animation_weapons: tuple[str, ...]
    shop_weapons: tuple[str, ...]
    vehicles: tuple[VehicleRecord, ...] = ()
    handlings: tuple[HandlingRecord, ...] = ()
    variations: tuple[VehicleVariationRecord, ...] = ()
    kits: tuple[VehicleKitRecord, ...] = ()
    registrations: tuple[PackageRegistrationRecord, ...] = ()
    binary_plugins: tuple[str, ...] = ()
    config_files: tuple[str, ...] = ()
    shader_assets: tuple[str, ...] = ()
    replacement_assets: tuple[str, ...] = ()
    package_kinds: tuple[str, ...] = ()
    edition_hints: tuple[str, ...] = ()
    installation_targets: tuple[str, ...] = ()
    dependency_hints: tuple[str, ...] = ()
    plugin_details: tuple[BinaryPluginRecord, ...] = ()
    weapon_components: tuple[WeaponComponentRecord, ...] = ()
    weapon_component_links: tuple[WeaponComponentLink, ...] = ()
    peds: tuple[PedRecord, ...] = ()
    weapon_animation_records: tuple[WeaponAnimationRecord, ...] = ()
    weapon_shop_records: tuple[WeaponShopRecord, ...] = ()
    weapon_enhancements: tuple[WeaponEnhancementContract, ...] = ()
    scripted_weapon_systems: tuple[ScriptedWeaponSystemRecord, ...] = ()
    rpf_archives: tuple[RpfPackageRecord, ...] = ()
    rpf_indexed_entries: tuple[PackageEntry, ...] = ()
    rpf_native_assets: tuple[RpfNativeEntryRecord, ...] = ()
    material_progressions: tuple[MaterialProgressionReport, ...] = ()

    @property
    def valid(self) -> bool:
        return not any(item.severity == "error" for item in self.findings)

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.findings)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.findings)

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries)

    @property
    def workbench_entries(self) -> tuple[PackageEntry, ...]:
        """Return directly readable assets visible to specialist workbenches.

        Normal packages keep their authored inventory. A directly opened RPF
        additionally exposes its recursively indexed entries by stable entry id;
        PackageAssetReader resolves those ids without extracting the archive first.
        """
        if self.source_kind != "rpf":
            return self.entries
        return self.entries + self.rpf_indexed_entries

    @property
    def edition_tag(self) -> str:
        editions = set(self.edition_hints)
        if editions == {"legacy", "enhanced"}:
            return "Legacy + Enhanced"
        if editions == {"legacy"}:
            return "Legacy"
        if editions == {"enhanced"}:
            return "Enhanced"
        return "Unresolved"

    @property
    def inspection_target_edition(self) -> str:
        """Return the decoder target without claiming authored compatibility."""
        if self.edition_tag in {"Legacy", "Enhanced"}:
            return self.edition_tag
        if self.source_kind == "rpf":
            targets = {
                item.edition.casefold() for item in self.rpf_archives
                if item.edition.casefold() in {"legacy", "enhanced"}
            }
            if len(targets) == 1:
                return targets.pop().title()
        return ""


@dataclass(frozen=True)
class ImportedAddonDraft:
    scan: PackageScan
    manifest: dict[str, Any]

    def write(self, destination: str | Path) -> Path:
        path = Path(destination).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(
            json.dumps(self.manifest, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
        return path


class AddonPackageInspector:
    """Read a loose DLC folder, direct RPF, or packaged add-on without installing it."""

    def __init__(
        self, project_root: str | Path | None = None,
        gta_path: str | Path | None = None,
    ) -> None:
        self.project_root = (
            Path(project_root).expanduser().resolve()
            if project_root is not None else None
        )
        self.gta_path = (
            Path(gta_path).expanduser().resolve()
            if gta_path is not None else None
        )

    def inspect(self, source: str | Path) -> PackageScan:
        path = Path(source).expanduser().resolve()
        if path.is_dir():
            source_kind = "folder"
            entries, findings = self._read_folder(path)
        elif path.is_file() and path.suffix.lower() in {".oiv", ".zip"}:
            source_kind = "oiv" if path.suffix.lower() == ".oiv" else "zip"
            entries, findings = self._read_zip(path, source_kind)
        elif path.is_file() and path.suffix.lower() in EXTERNAL_ARCHIVE_SUFFIXES:
            source_kind = path.suffix.lower().lstrip(".")
            entries, findings = self._read_external_archive(path)
        elif path.is_file() and path.suffix.lower() == ".rpf":
            if self.project_root is None or self.gta_path is None:
                raise ValueError(
                    "Opening a direct .rpf requires a matching GTA V path so the "
                    "SDK can index encrypted and native entries safely"
                )
            size = path.stat().st_size
            if size <= 0 or size > MAX_DIRECT_RPF_BYTES:
                raise ValueError("Direct RPF is empty or exceeds the 4 GiB inspection limit")
            source_kind = "rpf"
            entries = [PackageEntry(path.name, size)]
            findings = []
        else:
            raise ValueError(
                "Select a DLC folder, direct .rpf, or an .oiv/.zip/.rar/.7z package"
            )

        managed_manifests = [
            entry for entry in entries
            if PurePosixPath(entry.path).name.casefold() == "mod.toml"
        ]
        manifest_editions: tuple[str, ...] = ()
        weapon_enhancements: tuple[WeaponEnhancementContract, ...] = ()
        scripted_weapon_systems: tuple[ScriptedWeaponSystemRecord, ...] = ()
        if len(managed_manifests) > 1:
            paths = ", ".join(entry.path for entry in managed_manifests[:4])
            if len(managed_manifests) > 4:
                paths += f" (+{len(managed_manifests) - 4} more)"
            findings.append(PackageFinding(
                "warning", "managed_manifest_ambiguous",
                "Multiple mod.toml manifests describe different possible package "
                f"roots: {paths}. Select one package root before installation.",
                managed_manifests[0].path,
            ))
        elif managed_manifests:
            manifest = managed_manifests[0]
            if manifest.content is not None:
                manifest_editions = self._managed_manifest_editions(
                    manifest, findings,
                )
                weapon_enhancements, scripted_weapon_systems = self._managed_workbench_contract(
                    entries, manifest, findings,
                )

        weapons: list[WeaponRecord] = []
        ammo: list[AmmoRecord] = []
        weapon_components: list[WeaponComponentRecord] = []
        weapon_component_links: list[WeaponComponentLink] = []
        weapon_animation_records: list[WeaponAnimationRecord] = []
        weapon_shop_records: list[WeaponShopRecord] = []
        vehicles: list[VehicleRecord] = []
        handlings: list[HandlingRecord] = []
        variations: list[VehicleVariationRecord] = []
        kits: list[VehicleKitRecord] = []
        peds: list[PedRecord] = []
        registrations: list[PackageRegistrationRecord] = []
        for entry in entries:
            if entry.content is None:
                continue
            if entry.suffix in MANIFEST_TEXT_SUFFIXES:
                registrations.extend(self._script_registration_records(
                    entry.path, decode_text_preview(entry.content),
                ))
                continue
            if entry.suffix not in XML_SUFFIXES:
                continue
            try:
                root = _parse_xml(entry.content, entry.path)
            except (ET.ParseError, ValueError) as exc:
                findings.append(PackageFinding(
                    "warning", "xml_parse_failed",
                    f"Could not parse XML metadata: {exc}", entry.path,
                ))
                continue
            found_weapons, found_ammo = self._metadata_records(entry.path, root)
            weapons.extend(found_weapons)
            ammo.extend(found_ammo)
            weapon_components.extend(
                self._weapon_component_records(entry.path, root)
            )
            weapon_component_links.extend(
                self._weapon_component_links(entry.path, root)
            )
            weapon_animation_records.extend(
                self._animation_records(entry.path, root)
            )
            weapon_shop_records.extend(
                self._shop_records(entry.path, root)
            )
            vehicles.extend(self._vehicle_records(entry.path, root))
            handlings.extend(self._handling_records(entry.path, root))
            variations.extend(self._variation_records(entry.path, root))
            kits.extend(self._kit_records(entry.path, root))
            peds.extend(self._ped_records(entry.path, root))
            registrations.extend(self._xml_registration_records(entry.path, root))

        binary_plugins = tuple(
            entry.path for entry in entries
            if entry.suffix in BINARY_PLUGIN_SUFFIXES
        )
        plugin_details = tuple(
            _binary_plugin_record(entry.path, entry.content)
            for entry in entries if entry.suffix in BINARY_PLUGIN_SUFFIXES
        )
        config_files = tuple(
            entry.path for entry in entries
            if entry.suffix in {".ini", ".toml", ".cfg", ".json"}
        )
        shader_assets = tuple(
            entry.path for entry in entries if entry.suffix in {".fx", ".fxh"}
        )
        replacement_assets = tuple(
            entry.path for entry in entries
            if entry.suffix in ASSET_SUFFIXES
            and ((not vehicles and not weapons and not peds and not binary_plugins) or any(
                part.casefold().endswith(".rpf")
                for part in PurePosixPath(entry.path).parts
            ))
        )
        edition_hints: list[str] = list(manifest_editions)
        for entry in entries:
            lowered_parts = {part.casefold() for part in PurePosixPath(entry.path).parts}
            if not manifest_editions and "legacy" in lowered_parts:
                edition_hints.append("legacy")
            if not manifest_editions and "enhanced" in lowered_parts:
                edition_hints.append("enhanced")

        installation_targets: list[str] = []
        dependency_hints: list[str] = []
        dependency_terms = {
            "scripthookvdotnet": "ScriptHookVDotNet",
            "shvdn": "ScriptHookVDotNet",
            "scripthookv": "ScriptHookV",
            "openrpf": "OpenRPF",
            "openiv": "OIV package",
            "reshade": "ReShade",
        }
        for entry in entries:
            parts = PurePosixPath(entry.path).parts
            if entry.suffix == ".rpf" and PurePosixPath(entry.path).name.casefold() == "dlc.rpf":
                pack_name = PurePosixPath(entry.path).parent.name
                if pack_name:
                    installation_targets.append(
                        f"mods/update/x64/dlcpacks/{pack_name}/dlc.rpf"
                    )
            else:
                for index, part in enumerate(parts):
                    if part.casefold().endswith(".rpf"):
                        installation_targets.append("/".join(parts[:index + 1]))
            if entry.content is None or entry.suffix not in {".txt", ".md"}:
                continue
            text = decode_text_preview(entry.content)
            lowered = text.casefold()
            if not manifest_editions and re.search(
                r"\b(?:gta\s*v\s*)?legacy\b|\bclassic(?:/legacy)?\s+version\b",
                lowered,
            ) and _documentation_supports_edition(lowered, "legacy"):
                edition_hints.append("legacy")
            if not manifest_editions and re.search(
                r"\b(?:gta\s*v\s*)?enhanced\b", lowered,
            ) and _documentation_supports_edition(lowered, "enhanced"):
                edition_hints.append("enhanced")
            for term, label in dependency_terms.items():
                if term in lowered:
                    dependency_hints.append(label)
            for line in text.splitlines():
                candidate_line = line.strip().strip("`\"'")
                if not re.match(r"(?i)^mods[\\/]", candidate_line):
                    continue
                match = re.search(
                    r"(?i)(?:mods[\\/])?[^\r\n]*?\.rpf(?:[\\/][^\r\n]*)?",
                    candidate_line,
                )
                if match:
                    installation_targets.append(
                        match.group(0).strip().replace("\\", "/")
                    )

        rpf_entries = [entry.path for entry in entries if entry.suffix == ".rpf"]
        rpf_inspection = RpfPackageInspection()
        if rpf_entries and self.project_root is not None and self.gta_path is not None:
            rpf_inspection = self._inspect_package_rpfs(
                path, entries, findings, weapon_enhancements,
            )
            vehicles.extend(rpf_inspection.vehicles)
            handlings.extend(rpf_inspection.handlings)
            variations.extend(rpf_inspection.variations)
            kits.extend(rpf_inspection.kits)
            registrations.extend(rpf_inspection.registrations)
            weapons.extend(rpf_inspection.weapons)
            ammo.extend(rpf_inspection.ammo)
            weapon_components.extend(rpf_inspection.weapon_components)
            weapon_component_links.extend(rpf_inspection.weapon_component_links)
            weapon_animation_records.extend(
                rpf_inspection.weapon_animation_records
            )
            weapon_shop_records.extend(rpf_inspection.weapon_shop_records)
            peds.extend(rpf_inspection.peds)
        else:
            for rpf_path in rpf_entries[:20]:
                findings.append(PackageFinding(
                    "warning", "opaque_rpf",
                    "Nested RPF content is inventoried but not inferred without a "
                    "matching GTA path; reopen the Workbench with --gta-path.",
                    rpf_path,
                ))
            if len(rpf_entries) > 20:
                findings.append(PackageFinding(
                    "warning", "opaque_rpf_summary",
                    f"{len(rpf_entries) - 20} additional RPF archives were omitted "
                    "from individual warnings.",
                ))
        rpf_archives = rpf_inspection.archives
        rpf_indexed_entries = rpf_inspection.indexed_entries
        rpf_native_assets = rpf_inspection.native_assets
        material_progressions = rpf_inspection.material_progressions

        package_kinds: list[str] = []
        suffixes = {PurePosixPath(path).suffix.casefold() for path in binary_plugins}
        if ".asi" in suffixes:
            package_kinds.append("asi_plugin")
        if ".dll" in suffixes:
            package_kinds.append("script_plugin")
        if ".addon64" in suffixes or shader_assets:
            package_kinds.append("reshade_addon")
        if any(entry.suffix == ".rpf" for entry in entries):
            package_kinds.append("dlc_archive")
        if replacement_assets:
            package_kinds.append("replacement_assets")
        if vehicles:
            package_kinds.append("vehicle_addon")
        if weapons:
            package_kinds.append("weapon_addon")
        if weapon_enhancements or scripted_weapon_systems:
            package_kinds.append("scripted_weapon_enhancement")
        if peds:
            package_kinds.append("ped_addon")
        if not package_kinds:
            package_kinds.append("data_or_unknown")

        if binary_plugins:
            findings.append(PackageFinding(
                "warning", "executable_payload_review_required",
                f"Package contains {len(binary_plugins)} compiled plug-in(s). "
                "They were inventoried but never loaded or executed.",
            ))
        invalid_plugins = [
            item.path for item in plugin_details if item.format != "pe"
        ]
        if invalid_plugins:
            findings.append(PackageFinding(
                "warning", "plugin_header_unrecognized",
                "Compiled plug-in headers could not be verified as PE files: "
                + ", ".join(invalid_plugins) + ".",
            ))
        x86_plugins = [
            item.path for item in plugin_details if item.architecture == "x86"
        ]
        if x86_plugins:
            findings.append(PackageFinding(
                "warning", "plugin_architecture_incompatible",
                "GTA V requires x64 plug-ins, but x86 payloads were detected: "
                + ", ".join(x86_plugins) + ".",
            ))
        if any(entry.suffix == ".pdb" for entry in entries):
            findings.append(PackageFinding(
                "info", "debug_symbols_present",
                "Package includes debug symbols; they are optional at runtime.",
            ))
        if replacement_assets:
            findings.append(PackageFinding(
                "warning", "replacement_assets_require_targets",
                "Replacement assets require exact current-build archive targets, "
                "per-entry backups, and edition-specific verification.",
            ))
        content_addons = {
            "vehicle_addon", "weapon_addon", "ped_addon",
            "scripted_weapon_enhancement",
        }.intersection(package_kinds)
        integration_shapes = [
            kind for kind in package_kinds
            if kind != "dlc_archive" or not content_addons
        ]
        if len(integration_shapes) > 1:
            findings.append(PackageFinding(
                "warning", "mixed_package_layout",
                "Package combines multiple integration shapes: "
                + ", ".join(integration_shapes) + ".",
            ))
        if not edition_hints:
            findings.append(PackageFinding(
                "warning", "edition_compatibility_unresolved",
                "The package contains no trustworthy Legacy/Enhanced declaration. "
                "Choose an edition only after inspecting its resources or author notes.",
            ))
        if source_kind == "rpf":
            findings.append(PackageFinding(
                "info", "direct_rpf_inspection_source",
                "A loose RPF is a sealed inspection source, not a complete managed "
                "package. Use Quick Import or extract an authoring workspace before "
                "packaging changes.",
            ))
        elif source_kind != "folder" and not managed_manifests:
            findings.append(PackageFinding(
                "warning", "managed_manifest_not_found",
                "No reviewed mod.toml was found. This archive can be inspected, "
                "but it is not directly installable by ALLIN1.",
            ))

        weapons = self._dedupe_records(
            weapons, lambda item: item.name.casefold(), findings,
        )
        ammo = self._dedupe_records(
            ammo, lambda item: item.name.casefold(), findings,
        )
        weapon_components = self._dedupe_records(
            weapon_components, lambda item: item.name.casefold(), findings,
        )
        weapon_component_links = self._dedupe_records(
            weapon_component_links,
            lambda item: (
                item.weapon_name.casefold(), item.component_name.casefold(),
                item.attach_bone.casefold(),
            ),
            findings,
        )
        vehicles = self._dedupe_records(
            vehicles, lambda item: (
                item.edition.casefold(), item.model_name.casefold(),
            ), findings,
        )
        handlings = self._dedupe_records(
            handlings, lambda item: (
                item.edition.casefold(), item.name.casefold(),
            ), findings,
        )
        variations = self._dedupe_records(
            variations, lambda item: (
                item.edition.casefold(), item.model_name.casefold(),
            ), findings,
        )
        kits = self._dedupe_records(
            kits, lambda item: (
                item.edition.casefold(), item.name.casefold(),
            ), findings,
        )
        peds = self._dedupe_records(
            peds, lambda item: item.name.casefold(), findings,
        )
        self._report_source_record_duplicates(
            weapon_animation_records,
            lambda item: (
                item.source.casefold(), item.set_ordinal,
                item.field_name.casefold(),
                item.weapon_name.casefold(),
            ),
            "duplicate_weapon_animation_record",
            "weapon animation",
            findings,
        )
        self._report_source_record_duplicates(
            weapon_shop_records,
            lambda item: (
                item.source.casefold(), item.field_name.casefold(),
                item.weapon_name.casefold(),
            ),
            "duplicate_weapon_shop_record",
            "weapon shop",
            findings,
        )
        animation_names = tuple(_unique_casefold(
            item.weapon_name for item in weapon_animation_records
        ))
        shop_names = tuple(_unique_casefold(
            item.weapon_name for item in weapon_shop_records
        ))
        ammo_names = {item.name.casefold() for item in ammo}
        animation_set = {item.casefold() for item in animation_names}
        shop_set = {item.casefold() for item in shop_names}
        component_names = {item.name.casefold() for item in weapon_components}
        for weapon in weapons:
            if not weapon.ammo_info:
                findings.append(PackageFinding(
                    "warning", "weapon_ammo_reference_missing",
                    f"{weapon.name} has no AmmoInfo reference.", weapon.source,
                ))
            elif weapon.ammo_info.casefold() not in ammo_names:
                findings.append(PackageFinding(
                    "warning", "ammo_definition_not_found",
                    f"{weapon.name} references {weapon.ammo_info}, but its definition "
                    "was not visible in the package.", weapon.source,
                ))
            if weapon.name.casefold() not in animation_set:
                findings.append(PackageFinding(
                    "warning", "animation_mapping_not_found",
                    f"No weaponanimations mapping was discovered for {weapon.name}.",
                    weapon.source,
                ))
            if weapon.name.casefold() not in shop_set:
                findings.append(PackageFinding(
                    "warning", "storefront_mapping_not_found",
                    f"No weapon_shop entry was discovered for {weapon.name}.",
                    weapon.source,
                ))
        for link in weapon_component_links:
            if link.component_name.casefold() not in component_names:
                findings.append(PackageFinding(
                    "warning", "weapon_component_definition_not_found",
                    f"{link.weapon_name} references {link.component_name}, but its "
                    "component definition was not visible in the package.",
                    link.source,
                ))

        handling_names = {item.name.casefold() for item in handlings}
        variation_names = {item.model_name.casefold() for item in variations}
        kit_names = {item.name.casefold() for item in kits}
        yft_models = {
            PurePosixPath(entry.path).stem.casefold()
            for entry in entries if entry.suffix == ".yft"
        }
        yft_models.update(
            PurePosixPath(entry.path).stem.casefold()
            for entry in rpf_native_assets if entry.suffix == ".yft"
        )
        ytd_models = {
            PurePosixPath(entry.path).stem.casefold()
            for entry in entries if entry.suffix == ".ytd"
        }
        ytd_models.update(
            PurePosixPath(entry.path).stem.casefold()
            for entry in rpf_native_assets if entry.suffix == ".ytd"
        )
        scoped_loose_vehicle_models: set[str] = set()
        for vehicle in vehicles:
            if not vehicle.handling_id:
                findings.append(PackageFinding(
                    "warning", "vehicle_handling_reference_missing",
                    f"{vehicle.model_name} has no handlingId.", vehicle.source,
                ))
            elif vehicle.handling_id.casefold() not in handling_names:
                findings.append(PackageFinding(
                    "warning", "handling_definition_not_found",
                    f"{vehicle.model_name} references {vehicle.handling_id}, but "
                    "that handling record was not visible in the package.",
                    vehicle.source,
                ))
            if vehicle.model_name.casefold() not in variation_names:
                findings.append(PackageFinding(
                    "warning", "vehicle_variation_not_found",
                    f"No carvariations record was discovered for {vehicle.model_name}.",
                    vehicle.source,
                ))
            txd_key = (vehicle.txd_name or vehicle.model_name).casefold()
            model_key = vehicle.model_name.casefold()
            has_related_loose_asset = bool(
                model_key in yft_models
                or f"{model_key}_hi" in yft_models
                or txd_key in ytd_models
            )
            if has_related_loose_asset:
                scoped_loose_vehicle_models.add(model_key)
            if has_related_loose_asset:
                if vehicle.model_name.casefold() not in yft_models:
                    findings.append(PackageFinding(
                        "warning", "vehicle_model_asset_not_found",
                        f"No streamed YFT was discovered for {vehicle.model_name}.",
                        vehicle.source,
                    ))
                if txd_key not in ytd_models:
                    findings.append(PackageFinding(
                        "warning", "vehicle_texture_asset_not_found",
                        f"No streamed YTD was discovered for "
                        f"{vehicle.txd_name or vehicle.model_name}.",
                        vehicle.source,
                    ))

        for variation in variations:
            for kit in variation.kits:
                if kit.casefold() in {"0_default_modkit", "default_modkit"}:
                    continue
                if kit.casefold() not in kit_names:
                    findings.append(PackageFinding(
                        "warning", "vehicle_kit_not_found",
                        f"{variation.model_name} references missing tuning kit {kit}.",
                        variation.source,
                    ))
        scoped_kit_names = {
            authored_kit.casefold()
            for variation in variations
            if variation.model_name.casefold() in scoped_loose_vehicle_models
            for authored_kit in variation.kits
        }
        for kit in kits:
            kit_is_scoped = (
                kit.name.casefold() in scoped_kit_names
                or kit.kit_id.casefold() in scoped_kit_names
                or any(name.casefold() in yft_models for name in kit.model_names)
            )
            if not kit_is_scoped:
                continue
            for model_name in kit.model_names:
                if model_name.casefold() not in yft_models:
                    findings.append(PackageFinding(
                        "warning", "tuning_model_asset_not_found",
                        f"Tuning kit {kit.name} references missing YFT "
                        f"{model_name}.", kit.source,
                    ))

        ydd_models = {
            PurePosixPath(entry.path).stem.casefold()
            for entry in entries if entry.suffix == ".ydd"
        }
        ydd_models.update(
            PurePosixPath(entry.path).stem.casefold()
            for entry in rpf_native_assets if entry.suffix == ".ydd"
        )
        ydr_models = {
            PurePosixPath(entry.path).stem.casefold()
            for entry in entries if entry.suffix == ".ydr"
        }
        ydr_models.update(
            PurePosixPath(entry.path).stem.casefold()
            for entry in rpf_native_assets if entry.suffix == ".ydr"
        )
        for ped in peds:
            model = ped.name.casefold()
            has_related_loose_asset = (
                model in ydd_models or model in ydr_models or model in ytd_models
            )
            if has_related_loose_asset:
                if model not in ydd_models and model not in ydr_models:
                    findings.append(PackageFinding(
                        "warning", "ped_model_asset_not_found",
                        f"No streamed YDD/YDR was discovered for {ped.name}.",
                        ped.source,
                    ))
                if model not in ytd_models:
                    findings.append(PackageFinding(
                        "warning", "ped_texture_asset_not_found",
                        f"No streamed YTD was discovered for {ped.name}.",
                        ped.source,
                    ))

        entry_paths = {entry.path.casefold() for entry in entries}
        for registration in registrations:
            if registration.kind != "fivem-resource":
                continue
            base = PurePosixPath(registration.source).parent
            for declared in registration.metadata_files:
                candidate = (base / declared).as_posix().casefold()
                if candidate not in entry_paths:
                    findings.append(PackageFinding(
                        "warning", "declared_metadata_file_not_found",
                        f"Resource manifest references missing file {declared}.",
                        registration.source,
                    ))
        if vehicles and not registrations:
            findings.append(PackageFinding(
                "warning", "vehicle_registration_not_found",
                "Vehicle metadata was discovered without content.xml/setup2.xml or "
                "a FiveM resource manifest.",
            ))

        nested_package_entries = [
            entry.path for entry in entries
            if entry.suffix in {".zip", ".oiv", ".rar", ".7z"}
        ]
        for nested_path in nested_package_entries[:20]:
            findings.append(PackageFinding(
                "info", "nested_package_not_inspected",
                "Nested package archives are inventoried as opaque members; inspect "
                "the nested package separately before relying on its contents.",
                nested_path,
            ))
        if len(nested_package_entries) > 20:
            findings.append(PackageFinding(
                "info", "nested_package_summary",
                f"{len(nested_package_entries) - 20} additional nested package "
                "archives were omitted from individual notices.",
            ))
        if (
            not weapons and not vehicles and not peds
            and not weapon_enhancements and not scripted_weapon_systems
        ):
            recognized_non_content = bool(
                binary_plugins or config_files or shader_assets
                or replacement_assets or rpf_entries or registrations
                or nested_package_entries
            )
            findings.append(PackageFinding(
                "info" if recognized_non_content else "warning",
                "no_content_records",
                "No custom weapon, vehicle, or ped records were discovered. The draft "
                "will describe the detected plug-in, replacement, shader, archive, "
                "or generic package shape instead.",
            ))
        if source_kind == "rpf":
            externally_resolvable = {
                "ammo_definition_not_found",
                "animation_mapping_not_found",
                "handling_definition_not_found",
                "ped_model_asset_not_found",
                "ped_texture_asset_not_found",
                "tuning_model_asset_not_found",
                "vehicle_handling_reference_missing",
                "vehicle_kit_not_found",
                "vehicle_model_asset_not_found",
                "vehicle_registration_not_found",
                "vehicle_texture_asset_not_found",
                "vehicle_variation_not_found",
                "weapon_component_definition_not_found",
            }
            findings = [
                replace(
                    finding,
                    severity="info",
                    message=(
                        finding.message
                        + " The reference is not present in this selected RPF; "
                        "it may resolve through base-game or shared DLC content."
                    ),
                )
                if finding.severity == "warning"
                and finding.code in externally_resolvable else finding
                for finding in findings
            ]

        return PackageScan(
            source=path,
            source_kind=source_kind,
            entries=tuple(entries),
            findings=tuple(findings),
            weapons=tuple(weapons),
            ammo=tuple(ammo),
            animation_weapons=animation_names,
            shop_weapons=shop_names,
            vehicles=tuple(vehicles),
            handlings=tuple(handlings),
            variations=tuple(variations),
            kits=tuple(kits),
            registrations=tuple(registrations),
            binary_plugins=binary_plugins,
            config_files=config_files,
            shader_assets=shader_assets,
            replacement_assets=replacement_assets,
            package_kinds=tuple(_unique(package_kinds)),
            edition_hints=tuple(_unique(edition_hints)),
            installation_targets=tuple(_unique(installation_targets)),
            dependency_hints=tuple(_unique(dependency_hints)),
            plugin_details=plugin_details,
            weapon_components=tuple(weapon_components),
            weapon_component_links=tuple(weapon_component_links),
            peds=tuple(peds),
            weapon_animation_records=tuple(weapon_animation_records),
            weapon_shop_records=tuple(weapon_shop_records),
            weapon_enhancements=weapon_enhancements,
            scripted_weapon_systems=scripted_weapon_systems,
            rpf_archives=rpf_archives,
            rpf_indexed_entries=rpf_indexed_entries,
            rpf_native_assets=rpf_native_assets,
            material_progressions=material_progressions,
        )

    @staticmethod
    def _managed_manifest_editions(
        manifest: PackageEntry, findings: list[PackageFinding],
    ) -> tuple[str, ...]:
        """Read only the authoritative edition declaration from one mod.toml."""
        assert manifest.content is not None
        try:
            payload = tomllib.loads(decode_text_preview(manifest.content))
        except tomllib.TOMLDecodeError as exc:
            findings.append(PackageFinding(
                "warning", "managed_manifest_parse_failed",
                f"Could not parse mod.toml metadata: {exc}", manifest.path,
            ))
            return ()
        raw_editions = payload.get("editions")
        if raw_editions is None:
            return ()
        if not isinstance(raw_editions, list) or not all(
            isinstance(value, str) for value in raw_editions
        ):
            findings.append(PackageFinding(
                "warning", "managed_manifest_editions_invalid",
                "mod.toml editions must be an array containing Legacy and/or "
                "Enhanced.", manifest.path,
            ))
            return ()
        editions = tuple(_unique(
            value.strip().casefold() for value in raw_editions if value.strip()
        ))
        if not editions or any(
            value not in {"legacy", "enhanced"} for value in editions
        ):
            findings.append(PackageFinding(
                "warning", "managed_manifest_editions_invalid",
                "mod.toml editions must contain only Legacy and/or Enhanced.",
                manifest.path,
            ))
            return ()
        return editions

    @staticmethod
    def _managed_workbench_contract(
        entries: list[PackageEntry], manifest: PackageEntry,
        findings: list[PackageFinding],
    ) -> tuple[
        tuple[WeaponEnhancementContract, ...],
        tuple[ScriptedWeaponSystemRecord, ...],
    ]:
        """Load explicitly declared script/vanilla/asset relationships."""
        assert manifest.content is not None
        try:
            package = tomllib.loads(decode_text_preview(manifest.content))
            raw_allin1 = package.get("allin1")
            if package.get("schema_version") != 2 or not isinstance(raw_allin1, dict):
                return (), ()
            relative = _safe_member_path(str(raw_allin1.get("content", "")))
            content_path = (
                PurePosixPath(manifest.path).parent / relative
            ).as_posix()
            matches = [
                entry for entry in entries
                if entry.path.casefold() == content_path.casefold()
            ]
            if len(matches) != 1 or matches[0].content is None:
                raise ValueError(
                    f"Declared ALLIN1 content manifest was not readable: {content_path}"
                )
            content = json.loads(decode_text_preview(matches[0].content))
            if not isinstance(content, dict):
                raise ValueError("ALLIN1 content manifest must be a JSON object")
            runtime = content.get("runtime", {})
            assemblies = (
                runtime.get("assemblies", []) if isinstance(runtime, dict) else []
            )
            entry_points = tuple(
                str(item.get("entry_point", "")).strip()
                for item in assemblies
                if isinstance(item, dict) and item.get("entry_point")
            )
            enhancements = parse_workbench_contract(
                content.get("workbench"), runtime_entry_points=entry_points,
            )
            capabilities = tuple(
                str(item).strip() for item in content.get("capabilities", [])
                if isinstance(item, str) and item.strip()
            )
            weapon_capabilities = tuple(
                item for item in capabilities
                if item.casefold().startswith("weapon.")
            )
            systems: list[ScriptedWeaponSystemRecord] = []
            for system in content.get("systems", []):
                if not isinstance(system, dict):
                    continue
                if (
                    str(system.get("category", "")).casefold() != "weapons"
                    and not weapon_capabilities
                ):
                    continue
                systems.append(ScriptedWeaponSystemRecord(
                    system_id=str(system.get("id", "")).strip(),
                    name=str(system.get("name", "")).strip(),
                    capabilities=weapon_capabilities,
                    script_entry_points=entry_points,
                    relationships_declared=bool(enhancements),
                ))
            if systems and not enhancements:
                findings.append(PackageFinding(
                    "info", "scripted_vanilla_weapon_system",
                    "Schema-2 runtime declares a script-driven vanilla weapon "
                    "enhancement. Add workbench.weapon_enhancements to expose "
                    "exact weapon, component, script, and visual-asset links.",
                    content_path,
                ))
            return enhancements, tuple(systems)
        except (
            json.JSONDecodeError, tomllib.TOMLDecodeError, TypeError, ValueError,
        ) as exc:
            findings.append(PackageFinding(
                "warning", "workbench_contract_invalid",
                f"Could not load declared Workbench relationships: {exc}",
                manifest.path,
            ))
            return (), ()

    def _inspect_package_rpfs(
        self, source: Path, entries: list[PackageEntry],
        findings: list[PackageFinding],
        weapon_enhancements: tuple[WeaponEnhancementContract, ...] = (),
    ) -> RpfPackageInspection:
        """Recursively index RPFs and promote bounded metadata into the graph."""
        assert self.project_root is not None and self.gta_path is not None
        from allin1_sdk.rpf_tools import RpfExplorerService

        members = [entry for entry in entries if entry.suffix == ".rpf"]
        if len(members) > MAX_RECURSIVE_RPF_MEMBERS:
            findings.append(PackageFinding(
                "warning", "rpf_inspection_limit",
                "Only the first "
                f"{MAX_RECURSIVE_RPF_MEMBERS} package RPF members were recursively inspected.",
            ))
        records: list[RpfPackageRecord] = []
        indexed_entries: list[PackageEntry] = []
        native: list[RpfNativeEntryRecord] = []
        material_reports: list[MaterialProgressionReport] = []
        vehicles: list[VehicleRecord] = []
        handlings: list[HandlingRecord] = []
        variations: list[VehicleVariationRecord] = []
        kits: list[VehicleKitRecord] = []
        registrations: list[PackageRegistrationRecord] = []
        weapons: list[WeaponRecord] = []
        ammo: list[AmmoRecord] = []
        weapon_components: list[WeaponComponentRecord] = []
        weapon_component_links: list[WeaponComponentLink] = []
        weapon_animation_records: list[WeaponAnimationRecord] = []
        weapon_shop_records: list[WeaponShopRecord] = []
        peds: list[PedRecord] = []
        declarations = tuple(
            visual
            for enhancement in weapon_enhancements
            for visual in enhancement.visual_assets
        )
        direct_rpf = source.is_file() and source.suffix.casefold() == ".rpf"
        reader = None if direct_rpf else PackageAssetReader(source)
        service = RpfExplorerService(self.project_root, self.gta_path)
        with tempfile.TemporaryDirectory(prefix="allin1-workbench-rpf-") as temporary:
            root = Path(temporary)
            for number, member in enumerate(
                members[:MAX_RECURSIVE_RPF_MEMBERS], start=1,
            ):
                try:
                    inspection_limit = (
                        MAX_DIRECT_RPF_BYTES if direct_rpf else 512 * 1024 * 1024
                    )
                    if member.size <= 0 or member.size > inspection_limit:
                        raise ValueError(
                            "RPF is empty or exceeds the guarded inspection limit"
                        )
                    if direct_rpf:
                        if number != 1 or member.path.casefold() != source.name.casefold():
                            raise ValueError("Direct RPF inventory did not match its source")
                        extracted = source
                    else:
                        assert reader is not None
                        content = reader.read(member.path, limit=member.size + 1)
                        if content.truncated or len(content.data) != member.size:
                            raise ValueError("RPF could not be read completely")
                        extracted = root / f"member-{number}.rpf"
                        extracted.write_bytes(content.data)
                    index = service.index(extracted)
                    edition = self._package_member_edition(member.path) or index.edition
                    records.append(RpfPackageRecord(
                        source=member.path,
                        edition=edition,
                        archive_count=len(index.archives),
                        entry_count=len(index.entries),
                        suffix_counts=index.suffix_counts(),
                        warnings=index.warnings,
                    ))
                    if direct_rpf:
                        findings.append(PackageFinding(
                            "info", "direct_rpf_target_edition",
                            f"The direct RPF was decoded against the selected "
                            f"{edition.title()} GTA installation. This identifies "
                            "the inspection target, not proven cross-edition "
                            "compatibility.",
                            member.path,
                        ))
                    if direct_rpf:
                        indexed_entries.extend(
                            PackageEntry(item.id, item.size)
                            for item in index.entries
                            if item.kind != "directory"
                        )
                    native.extend(
                        RpfNativeEntryRecord(
                            source=member.path,
                            archive_path=item.archive_path,
                            path=item.path,
                            entry_id=item.id,
                            kind=item.kind,
                            suffix=item.suffix,
                            size=item.size,
                        )
                        for item in index.entries
                        if item.kind != "directory" and item.suffix in ASSET_SUFFIXES
                    )
                    findings.append(PackageFinding(
                        "info", "rpf_recursively_inspected",
                        f"Recursively inspected {len(index.archives)} archive layer(s) "
                        f"and {len(index.entries)} entries.",
                        member.path,
                    ))
                    metadata_entries = [
                        item for item in index.entries
                        if item.kind != "directory" and item.suffix in XML_SUFFIXES
                    ]
                    if len(metadata_entries) > 256:
                        findings.append(PackageFinding(
                            "warning", "rpf_metadata_inspection_limit",
                            "Only the first 256 XML/META entries were considered "
                            "for package relationships.", member.path,
                        ))
                    selected_metadata = []
                    for item in metadata_entries[:256]:
                        virtual_source = (
                            item.id if direct_rpf
                            else self._rpf_virtual_source(member.path, item)
                        )
                        if item.size <= 0 or item.size > MAX_XML_BYTES:
                            findings.append(PackageFinding(
                                "warning", "rpf_metadata_size_unsupported",
                                "Nested metadata is empty or exceeds the 16 MiB XML "
                                "inspection limit.", virtual_source,
                            ))
                            continue
                        selected_metadata.append(item)
                    extracted_metadata: tuple[Path, ...] = ()
                    if selected_metadata:
                        try:
                            extracted_metadata = service.extract_many(
                                index, selected_metadata,
                                root / f"metadata-{number}",
                            )
                        except (OSError, RuntimeError, ValueError) as exc:
                            findings.append(PackageFinding(
                                "warning", "rpf_metadata_extract_failed",
                                f"Could not extract nested metadata as one verified "
                                f"batch: {exc}", member.path,
                            ))
                    promoted = 0
                    for item, destination in zip(
                        selected_metadata, extracted_metadata,
                    ):
                        virtual_source = (
                            item.id if direct_rpf
                            else self._rpf_virtual_source(member.path, item)
                        )
                        try:
                            metadata = destination.read_bytes()
                            xml_root = _parse_xml(metadata, virtual_source)
                        except (ET.ParseError, OSError, RuntimeError, ValueError) as exc:
                            findings.append(PackageFinding(
                                "warning", "rpf_metadata_parse_failed",
                                f"Could not promote nested XML metadata: {exc}",
                                virtual_source,
                            ))
                            continue
                        found_vehicles = self._vehicle_records(
                            virtual_source, xml_root,
                        )
                        found_handlings = self._handling_records(
                            virtual_source, xml_root,
                        )
                        found_variations = self._variation_records(
                            virtual_source, xml_root,
                        )
                        found_kits = self._kit_records(virtual_source, xml_root)
                        found_registrations = self._xml_registration_records(
                            virtual_source, xml_root,
                        )
                        found_weapons, found_ammo = self._metadata_records(
                            virtual_source, xml_root,
                        )
                        found_components = self._weapon_component_records(
                            virtual_source, xml_root,
                        )
                        found_component_links = self._weapon_component_links(
                            virtual_source, xml_root,
                        )
                        found_animations = self._animation_records(
                            virtual_source, xml_root,
                        )
                        found_shop_records = self._shop_records(
                            virtual_source, xml_root,
                        )
                        found_peds = self._ped_records(virtual_source, xml_root)
                        vehicles.extend(
                            replace(item, edition=edition) for item in found_vehicles
                        )
                        handlings.extend(
                            replace(item, edition=edition) for item in found_handlings
                        )
                        variations.extend(
                            replace(item, edition=edition) for item in found_variations
                        )
                        kits.extend(
                            replace(item, edition=edition) for item in found_kits
                        )
                        registrations.extend(found_registrations)
                        weapons.extend(found_weapons)
                        ammo.extend(found_ammo)
                        weapon_components.extend(found_components)
                        weapon_component_links.extend(found_component_links)
                        weapon_animation_records.extend(found_animations)
                        weapon_shop_records.extend(found_shop_records)
                        peds.extend(found_peds)
                        promoted += sum((
                            len(found_vehicles), len(found_handlings),
                            len(found_variations), len(found_kits),
                            len(found_registrations), len(found_weapons),
                            len(found_ammo), len(found_components),
                            len(found_component_links), len(found_animations),
                            len(found_shop_records), len(found_peds),
                        ))
                    if promoted:
                        findings.append(PackageFinding(
                            "info", "rpf_metadata_promoted",
                            f"Promoted {promoted} vehicle and registration record(s) "
                            "from nested RPF metadata into the package graph.",
                            member.path,
                        ))
                    try:
                        audited = audit_material_progressions(
                            service, index, root / f"material-{number}",
                            source=member.path, declarations=declarations,
                        )
                        material_reports.extend(audited)
                        for report in audited:
                            findings.append(PackageFinding(
                                "info", "material_progression_audited",
                                f"Audited {report.model_count} YDR tiers, "
                                f"{report.texture_count} YTD textures, and "
                                f"{report.archetype_count} YTYP archetypes.",
                                member.path,
                            ))
                    except (OSError, RuntimeError, ValueError) as exc:
                        findings.append(PackageFinding(
                            "warning", "material_progression_audit_failed",
                            f"Material progression audit could not complete: {exc}",
                            member.path,
                        ))
                except (OSError, RuntimeError, ValueError) as exc:
                    findings.append(PackageFinding(
                        "warning", "rpf_recursive_inspection_failed",
                        f"Could not recursively inspect this RPF: {exc}",
                        member.path,
                    ))
        return RpfPackageInspection(
            archives=tuple(records), indexed_entries=tuple(indexed_entries),
            native_assets=tuple(native),
            material_progressions=tuple(material_reports),
            vehicles=tuple(vehicles), handlings=tuple(handlings),
            variations=tuple(variations), kits=tuple(kits),
            registrations=tuple(registrations),
            weapons=tuple(weapons), ammo=tuple(ammo),
            weapon_components=tuple(weapon_components),
            weapon_component_links=tuple(weapon_component_links),
            weapon_animation_records=tuple(weapon_animation_records),
            weapon_shop_records=tuple(weapon_shop_records),
            peds=tuple(peds),
        )

    @staticmethod
    def _package_member_edition(path: str) -> str:
        parts = {part.casefold() for part in PurePosixPath(path).parts}
        found = [edition for edition in ("legacy", "enhanced") if edition in parts]
        return found[0] if len(found) == 1 else ""

    @staticmethod
    def _rpf_virtual_source(member_path: str, entry: object) -> str:
        archive_path = str(getattr(entry, "archive_path", "")).strip("/")
        path = str(getattr(entry, "path", "")).strip("/")
        segments = [member_path]
        if archive_path:
            segments.append(archive_path)
        segments.append(path)
        return "!".join(segments)

    def _read_external_archive(
        self, archive: Path,
    ) -> tuple[list[PackageEntry], list[PackageFinding]]:
        listed = _list_external_archive(archive)
        if len(listed) > MAX_PACKAGE_FILES:
            raise ValueError(
                f"Package contains more than {MAX_PACKAGE_FILES:,} files"
            )
        total = sum(size for _, size in listed)
        if total > MAX_PACKAGE_BYTES:
            raise ValueError(
                "Package exceeds the 2 GiB inspection limit. If this is a DLC "
                "archive, open its dlc.rpf directly instead of zipping it."
            )
        entries: list[PackageEntry] = []
        findings: list[PackageFinding] = []
        seen_paths: set[str] = set()
        for relative, size in listed:
            normalized = relative.casefold()
            if normalized in seen_paths:
                findings.append(PackageFinding(
                    "error", "duplicate_member",
                    "Duplicate archive member paths are ambiguous and cannot "
                    "be imported safely.", relative,
                ))
            seen_paths.add(normalized)
            suffix = PurePosixPath(relative).suffix.lower()
            content = None
            if suffix in INSPECTION_TEXT_SUFFIXES:
                if size <= MAX_XML_BYTES:
                    content, truncated = _read_external_archive_member(
                        archive, relative, limit=MAX_XML_BYTES,
                    )
                    if truncated or len(content) != size:
                        findings.append(PackageFinding(
                            "error", "archive_member_size_mismatch",
                            "Archive member did not match its declared size.", relative,
                        ))
                        content = None
                else:
                    findings.append(_inspection_size_finding(suffix, relative))
            elif suffix in BINARY_PLUGIN_SUFFIXES:
                content, _ = _read_external_archive_member(
                    archive, relative, limit=min(size, MAX_BINARY_HEADER_BYTES),
                )
            entries.append(PackageEntry(relative, size, content))
        return entries, findings

    def _read_folder(
        self, root: Path,
    ) -> tuple[list[PackageEntry], list[PackageFinding]]:
        entries: list[PackageEntry] = []
        findings: list[PackageFinding] = []
        pending_reads: list[tuple[int, Path, bool]] = []
        total = 0
        for candidate in sorted(root.rglob("*")):
            if candidate.is_symlink():
                findings.append(PackageFinding(
                    "warning", "symlink_skipped",
                    "Symbolic links are not followed during package inspection.",
                    candidate.relative_to(root).as_posix(),
                ))
                continue
            if not candidate.is_file():
                continue
            relative = candidate.relative_to(root).as_posix()
            _safe_member_path(relative)
            size = candidate.stat().st_size
            total += size
            if len(entries) >= MAX_PACKAGE_FILES:
                raise ValueError(
                    f"Package contains more than {MAX_PACKAGE_FILES:,} files"
                )
            if total > MAX_PACKAGE_BYTES:
                raise ValueError(
                    "Package exceeds the 2 GiB inspection limit. If this is a DLC "
                    "archive, open its dlc.rpf directly instead of zipping it."
                )
            content = None
            if candidate.suffix.lower() in INSPECTION_TEXT_SUFFIXES:
                if size <= MAX_XML_BYTES:
                    pending_reads.append((len(entries), candidate, False))
                else:
                    findings.append(_inspection_size_finding(
                        candidate.suffix.lower(), relative,
                    ))
            elif candidate.suffix.lower() in BINARY_PLUGIN_SUFFIXES:
                pending_reads.append((len(entries), candidate, True))
            entries.append(PackageEntry(relative, size, content))

        def read_content(request: tuple[int, Path, bool]) -> tuple[int, bytes]:
            index, candidate, bounded = request
            if bounded:
                with candidate.open("rb") as stream:
                    return index, stream.read(MAX_BINARY_HEADER_BYTES)
            return index, candidate.read_bytes()

        if len(pending_reads) < 4:
            loaded = map(read_content, pending_reads)
        else:
            workers = min(MAX_FOLDER_READ_WORKERS, len(pending_reads))
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="allin1-package-read",
            ) as executor:
                loaded = tuple(executor.map(read_content, pending_reads))
        for index, content in loaded:
            entry = entries[index]
            entries[index] = PackageEntry(entry.path, entry.size, content)
        return entries, findings

    def _read_zip(
        self, archive: Path, source_kind: str,
    ) -> tuple[list[PackageEntry], list[PackageFinding]]:
        entries: list[PackageEntry] = []
        findings: list[PackageFinding] = []
        try:
            with zipfile.ZipFile(archive) as package:
                members = [item for item in package.infolist() if not item.is_dir()]
                if len(members) > MAX_PACKAGE_FILES:
                    raise ValueError(
                        f"Package contains more than {MAX_PACKAGE_FILES:,} files"
                    )
                total = sum(item.file_size for item in members)
                if total > MAX_PACKAGE_BYTES:
                    raise ValueError(
                        "Package exceeds the 2 GiB inspection limit. If this is a "
                        "DLC archive, open its dlc.rpf directly instead of zipping it."
                    )
                seen_paths: set[str] = set()
                for member in members:
                    relative = _safe_member_path(member.filename).as_posix()
                    normalized = relative.casefold()
                    if normalized in seen_paths:
                        findings.append(PackageFinding(
                            "error", "duplicate_member",
                            "Duplicate archive member paths are ambiguous and cannot "
                            "be imported safely.", relative,
                        ))
                    seen_paths.add(normalized)
                    if member.flag_bits & 0x1:
                        findings.append(PackageFinding(
                            "error", "encrypted_member",
                            "Encrypted package members cannot be inspected.", relative,
                        ))
                        entries.append(PackageEntry(relative, member.file_size))
                        continue
                    suffix = PurePosixPath(relative).suffix.lower()
                    content = None
                    if suffix in INSPECTION_TEXT_SUFFIXES:
                        if member.file_size <= MAX_XML_BYTES:
                            content = package.read(member)
                        else:
                            findings.append(_inspection_size_finding(
                                suffix, relative,
                            ))
                    elif suffix in BINARY_PLUGIN_SUFFIXES:
                        with package.open(member) as stream:
                            content = stream.read(MAX_BINARY_HEADER_BYTES)
                    entries.append(PackageEntry(
                        relative, member.file_size, content,
                    ))
        except zipfile.BadZipFile as exc:
            raise ValueError(f"Invalid {source_kind.upper()} archive: {exc}") from exc
        if source_kind == "oiv" and not any(
            entry.path.lower() == "assembly.xml" for entry in entries
        ):
            findings.append(PackageFinding(
                "error", "oiv_assembly_missing",
                "An OIV package must contain assembly.xml at its root.",
            ))
        return entries, findings

    @staticmethod
    def _direct_value(element: ET.Element, name: str) -> str:
        for child in element:
            if _local_name(child.tag) != name:
                continue
            reference = child.attrib.get("ref", "").strip()
            if reference:
                return reference
            value = child.attrib.get("value", "").strip()
            if value:
                return value
            return (child.text or "").strip()
        return ""

    @classmethod
    def _metadata_records(
        cls, source: str, root: ET.Element,
    ) -> tuple[list[WeaponRecord], list[AmmoRecord]]:
        weapons: list[WeaponRecord] = []
        ammo: list[AmmoRecord] = []
        for item in root.iter():
            if _local_name(item.tag) != "Item":
                continue
            name = cls._direct_value(item, "Name")
            if _identifier_starts_with(name, "WEAPON_") and any(
                cls._direct_value(item, field)
                for field in ("Slot", "AmmoInfo", "HumanNameHash")
            ):
                weapons.append(WeaponRecord(
                    source, name, cls._direct_value(item, "Slot"),
                    cls._direct_value(item, "AmmoInfo"),
                    cls._direct_value(item, "Model"),
                    cls._direct_value(item, "HumanNameHash"),
                    cls._direct_value(item, "StatName"),
                ))
            elif _identifier_starts_with(name, "AMMO_"):
                ammo.append(AmmoRecord(
                    source, name, cls._direct_value(item, "Model"),
                    cls._direct_value(item, "AmmoMax"),
                    cls._direct_value(item, "AmmoMax50"),
                    cls._direct_value(item, "Explosion"),
                    cls._direct_value(item, "TrailFx"),
                    cls._direct_value(item, "PrimedFx"),
                ))
        return weapons, ammo

    @classmethod
    def _weapon_component_records(
        cls, source: str, root: ET.Element,
    ) -> list[WeaponComponentRecord]:
        records: list[WeaponComponentRecord] = []
        for item in root.iter():
            if _local_name(item.tag) != "Item":
                continue
            name = cls._direct_value(item, "Name")
            component_type = item.attrib.get("type", "").strip()
            if not _identifier_starts_with(name, "COMPONENT_"):
                continue
            model = cls._direct_value(item, "Model")
            loc_name = cls._direct_value(item, "LocName")
            loc_desc = cls._direct_value(item, "LocDesc")
            attach_bone = cls._direct_value(item, "AttachBone")
            if not any((model, loc_name, loc_desc, attach_bone, component_type)):
                continue
            records.append(WeaponComponentRecord(
                source, name, model, loc_name, loc_desc, attach_bone,
                component_type,
            ))
        return records

    @classmethod
    def _weapon_component_links(
        cls, source: str, root: ET.Element,
    ) -> list[WeaponComponentLink]:
        records: list[WeaponComponentLink] = []
        for weapon in root.iter():
            if _local_name(weapon.tag) != "Item":
                continue
            weapon_name = cls._direct_value(weapon, "Name")
            if not _identifier_starts_with(weapon_name, "WEAPON_"):
                continue
            for attach_points in weapon:
                if _local_name(attach_points.tag) != "AttachPoints":
                    continue
                for attach in attach_points:
                    if _local_name(attach.tag) != "Item":
                        continue
                    attach_bone = cls._direct_value(attach, "AttachBone")
                    for components in attach:
                        if _local_name(components.tag) != "Components":
                            continue
                        for component in components:
                            if _local_name(component.tag) != "Item":
                                continue
                            component_name = cls._direct_value(component, "Name")
                            if not _identifier_starts_with(
                                component_name, "COMPONENT_",
                            ):
                                continue
                            default_text = cls._direct_value(
                                component, "Default",
                            ).casefold()
                            records.append(WeaponComponentLink(
                                source, weapon_name, component_name, attach_bone,
                                default_text in {"1", "true", "yes"},
                            ))
        return records

    @classmethod
    def _animation_records(
        cls, source: str, root: ET.Element,
    ) -> list[WeaponAnimationRecord]:
        records: list[WeaponAnimationRecord] = []
        parents = {
            child: parent for parent in root.iter() for child in parent
        }
        groups = [
            item for item in root.iter()
            if _local_name(item.tag) == "WeaponAnimations"
        ]
        for set_ordinal, group in enumerate(groups):
            parent = parents.get(group)
            while parent is not None and _local_name(parent.tag) != "Item":
                parent = parents.get(parent)
            set_name = ""
            if parent is not None:
                set_name = (
                    parent.attrib.get("key", "").strip()
                    or cls._direct_value(parent, "Name")
                )
            for item in (
                child for child in group
                if _local_name(child.tag) == "Item"
            ):
                weapon_name = item.attrib.get("key", "").strip()
                if not _identifier_starts_with(weapon_name, "WEAPON_"):
                    continue
                records.append(WeaponAnimationRecord(
                    source=source,
                    weapon_name=weapon_name,
                    field_name="key",
                    representation="attribute",
                    set_name=set_name,
                    set_ordinal=set_ordinal,
                    ordinal=len(records),
                ))
        return records

    @staticmethod
    def _shop_records(
        source: str, root: ET.Element,
    ) -> list[WeaponShopRecord]:
        records: list[WeaponShopRecord] = []
        containers = [
            element for element in root.iter()
            if _local_name(element.tag) == "weaponShopItems"
        ]
        if containers:
            # Native shop metadata owns weapon identities only on direct Item
            # children of weaponShopItems.  Descendant component offers can carry
            # similarly named fields and must never be mistaken for weapons.
            owners = [
                item for container in containers for item in container
                if _local_name(item.tag) == "Item"
            ]
        else:
            # Retain support for small extracted/fixture fragments which omit the
            # native wrapper, while still requiring an Item owner and a direct
            # identity child rather than scraping arbitrary descendants.
            owners = [
                item for item in root.iter() if _local_name(item.tag) == "Item"
            ]
        for owner in owners:
            for item in owner:
                field_name = _local_name(item.tag)
                if field_name not in {"nameHash", "weaponName"}:
                    continue
                text_value = (item.text or "").strip()
                if text_value:
                    weapon_name = text_value
                    representation = "text"
                elif item.attrib.get("value", "").strip():
                    weapon_name = item.attrib["value"].strip()
                    representation = "value"
                elif item.attrib.get("ref", "").strip():
                    weapon_name = item.attrib["ref"].strip()
                    representation = "ref"
                else:
                    continue
                if not _identifier_starts_with(weapon_name, "WEAPON_"):
                    continue
                records.append(WeaponShopRecord(
                    source=source,
                    weapon_name=weapon_name,
                    field_name=field_name,
                    representation=representation,
                    ordinal=len(records),
                ))
        return records

    @classmethod
    def _vehicle_records(
        cls, source: str, root: ET.Element,
    ) -> list[VehicleRecord]:
        records: list[VehicleRecord] = []
        vehicle_document = "vehiclemodelinfo" in _local_name(root.tag).casefold()
        for container in root.iter():
            if _local_name(container.tag) != "InitDatas":
                continue
            for item in container:
                if _local_name(item.tag) != "Item":
                    continue
                model = cls._direct_value(item, "modelName")
                if not model:
                    continue
                # weaponarchetypes.meta also contains an InitDatas/modelName
                # shape.  Only promote canonical vehicle documents, or a
                # structurally complete vehicle entry from a wrapper document.
                vehicle_type = cls._direct_value(item, "type")
                if not vehicle_document:
                    handling = cls._direct_value(item, "handlingId")
                    vehicle_shape = (
                        cls._direct_value(item, "vehicleClass")
                        or vehicle_type.casefold().startswith("vehicle_type_")
                    )
                    if not handling or not vehicle_shape:
                        continue
                records.append(VehicleRecord(
                    source, model, cls._direct_value(item, "txdName"),
                    cls._direct_value(item, "handlingId"),
                    cls._direct_value(item, "gameName"),
                    cls._direct_value(item, "vehicleMakeName"),
                    cls._direct_value(item, "audioNameHash"),
                    cls._direct_value(item, "layout"),
                    vehicle_type,
                    cls._direct_value(item, "vehicleClass"),
                ))
        return records

    @classmethod
    def _ped_records(
        cls, source: str, root: ET.Element,
    ) -> list[PedRecord]:
        records: list[PedRecord] = []
        for container in root.iter():
            if _local_name(container.tag) != "InitDatas":
                continue
            for item in container:
                if _local_name(item.tag) != "Item":
                    continue
                # vehicles.meta uses lower-case modelName while peds.meta uses
                # the game-facing Name field. Keep the two record families
                # disjoint even when both appear in one imported package.
                if cls._direct_value(item, "modelName"):
                    continue
                name = cls._direct_value(item, "Name")
                ped_type = cls._direct_value(item, "Pedtype")
                props_name = cls._direct_value(item, "PropsName")
                movement = cls._direct_value(item, "MovementClipSet")
                creature = cls._direct_value(item, "CreatureMetadataName")
                if not name or not any((ped_type, props_name, movement, creature)):
                    continue
                records.append(PedRecord(
                    source=source,
                    name=name,
                    ped_type=ped_type,
                    model_type=cls._direct_value(item, "ModelType"),
                    props_name=props_name,
                    clip_dictionary=cls._direct_value(
                        item, "ClipDictionaryName",
                    ),
                    expression_set=cls._direct_value(item, "ExpressionSetName"),
                    movement_clip_set=movement,
                    creature_metadata=creature,
                ))
        return records

    @classmethod
    def _handling_records(
        cls, source: str, root: ET.Element,
    ) -> list[HandlingRecord]:
        records: list[HandlingRecord] = []
        for container in root.iter():
            if _local_name(container.tag) != "HandlingData":
                continue
            for item in container:
                if _local_name(item.tag) != "Item":
                    continue
                name = cls._direct_value(item, "handlingName")
                if name:
                    records.append(HandlingRecord(source, name))
        return records

    @classmethod
    def _variation_records(
        cls, source: str, root: ET.Element,
    ) -> list[VehicleVariationRecord]:
        records: list[VehicleVariationRecord] = []
        for container in root.iter():
            if _local_name(container.tag) != "variationData":
                continue
            for item in container:
                if _local_name(item.tag) != "Item":
                    continue
                model = cls._direct_value(item, "modelName")
                if not model:
                    continue
                kits: list[str] = []
                for child in item:
                    if _local_name(child.tag) != "kits":
                        continue
                    kits.extend(
                        (kit.text or "").strip() for kit in child
                        if _local_name(kit.tag) == "Item"
                    )
                records.append(VehicleVariationRecord(
                    source, model, tuple(_unique(kits)),
                    cls._direct_value(item, "lightSettings"),
                ))
        return records

    @classmethod
    def _kit_records(
        cls, source: str, root: ET.Element,
    ) -> list[VehicleKitRecord]:
        records: list[VehicleKitRecord] = []
        for container in root.iter():
            if _local_name(container.tag) != "Kits":
                continue
            for item in container:
                if _local_name(item.tag) != "Item":
                    continue
                name = cls._direct_value(item, "kitName")
                if not name:
                    continue
                models = _unique(
                    (element.text or "").strip()
                    for element in item.iter()
                    if _local_name(element.tag) == "modelName"
                )
                records.append(VehicleKitRecord(
                    source, name, cls._direct_value(item, "id"), tuple(models),
                ))
        return records

    @classmethod
    def _xml_registration_records(
        cls, source: str, root: ET.Element,
    ) -> list[PackageRegistrationRecord]:
        root_name = _local_name(root.tag)
        if root_name == "SSetupData":
            package_names = _unique((
                cls._direct_value(root, "deviceName"),
                cls._direct_value(root, "nameHash"),
            ))
            return [PackageRegistrationRecord(
                source, "single-player-setup", tuple(package_names), (),
            )]
        if root_name != "CDataFileMgr__ContentsOfDataFileXml":
            return []
        filenames: list[str] = []
        packages: list[str] = []
        for element in root.iter():
            if _local_name(element.tag) != "filename":
                continue
            value = (element.text or "").strip()
            if not value:
                continue
            filenames.append(value.rsplit("/", 1)[-1])
            if ":" in value:
                packages.append(value.split(":", 1)[0])
        return [PackageRegistrationRecord(
            source, "single-player-content", tuple(_unique(packages)),
            tuple(_unique(filenames)),
        )]

    @staticmethod
    def _script_registration_records(
        source: str, text: str,
    ) -> list[PackageRegistrationRecord]:
        name = PurePosixPath(source).name.casefold()
        if name not in {"__resource.lua", "fxmanifest.lua"}:
            return []
        metadata = _unique(
            match.group(1).replace("\\", "/")
            for match in re.finditer(
                r"['\"]([^'\"]+\.(?:meta|xml))['\"]", text,
                flags=re.IGNORECASE,
            )
        )
        package_name = PurePosixPath(source).parent.name
        return [PackageRegistrationRecord(
            source, "fivem-resource", (package_name,) if package_name else (),
            tuple(metadata),
        )]

    @staticmethod
    def _dedupe_records(records, key, findings):
        result = []
        seen: set[str] = set()
        for record in records:
            identifier = key(record)
            if identifier in seen:
                findings.append(PackageFinding(
                    "warning", "duplicate_record",
                    f"Duplicate metadata record ignored: {identifier}",
                    record.source,
                ))
                continue
            seen.add(identifier)
            result.append(record)
        return result

    @staticmethod
    def _report_source_record_duplicates(
        records, key, code: str, label: str, findings,
    ) -> None:
        """Report ambiguous authoring targets without discarding their evidence."""
        seen: set[object] = set()
        for record in records:
            identifier = key(record)
            if identifier not in seen:
                seen.add(identifier)
                continue
            findings.append(PackageFinding(
                "warning",
                code,
                f"Duplicate {label} record retained for authoring review: "
                f"{record.weapon_name}",
                record.source,
            ))


class AddonDraftBuilder:
    """Convert discovered package facts into an intentionally reviewable draft."""

    def build(self, scan: PackageScan) -> ImportedAddonDraft:
        folder_sources = scan.source_kind == "folder"
        source_by_suffix: dict[str, str] = {}
        for entry in scan.entries:
            source_by_suffix.setdefault(entry.suffix, entry.path)

        def sourced(node: dict[str, Any], source: str | None) -> dict[str, Any]:
            if folder_sources and source:
                node["source"] = source
            return node

        nodes: list[dict[str, Any]] = [sourced({
            "id": "package.imported",
            "kind": "package",
            "label": "Imported package inventory",
            "description": (
                "Generated from a read-only package scan. Review every inferred "
                "field before using it as an installation contract."
            ),
            "fields": {
                "Registration": (
                    "OIV assembly.xml" if scan.source_kind == "oiv"
                    else "Loose DLC/package folder"
                ),
                "Edition": scan.edition_tag,
                "Safety": "Draft only; no archive writes have been authorized",
                "Files": len(scan.entries),
                "Bytes": scan.total_bytes,
                "ImportedFrom": scan.source.name,
                "PackageKinds": list(scan.package_kinds),
                "EditionHints": list(scan.edition_hints),
                "InstallationTargets": list(scan.installation_targets),
                "DependencyHints": list(scan.dependency_hints),
            },
        }, "assembly.xml" if scan.source_kind == "folder" and
            any(item.path.lower() == "assembly.xml" for item in scan.entries)
            else None)]

        references: list[dict[str, Any]] = []
        companion_assets = [
            entry.path for entry in scan.entries
            if entry.path not in scan.binary_plugins
            and entry.path not in scan.config_files
            and entry.suffix not in {".pdb", ".txt", ".md"}
        ]
        plugin_details = {item.path: item for item in scan.plugin_details}
        script_binaries = [
            path for path in scan.binary_plugins
            if PurePosixPath(path).suffix.casefold() == ".dll"
        ]
        if script_binaries:
            nodes.append(sourced({
                "id": "scripts.imported", "kind": "script_plugin",
                "label": f"Discovered .NET script plug-ins ({len(script_binaries)})",
                "description": (
                    "Compiled DLLs were inventoried without loading them. Confirm "
                    "the intended ScriptHookVDotNet version and game edition."
                ),
                "fields": {
                    "Binaries": script_binaries,
                    "Architecture": {
                        path: plugin_details[path].architecture
                        for path in script_binaries
                    },
                    "Managed": {
                        path: plugin_details[path].managed for path in script_binaries
                    },
                    "Configuration": list(scan.config_files),
                    "CompanionAssets": companion_assets,
                    "DependencyHints": list(scan.dependency_hints),
                    "InstallRoot": "Unresolved; commonly the GTA V scripts directory",
                },
            }, script_binaries[0]))
        asi_binaries = [
            path for path in scan.binary_plugins
            if PurePosixPath(path).suffix.casefold() == ".asi"
        ]
        if asi_binaries:
            nodes.append(sourced({
                "id": "asi.imported", "kind": "asi_plugin",
                "label": f"Discovered ASI plug-ins ({len(asi_binaries)})",
                "description": (
                    "Native ASI plug-ins were inventoried without loading them. "
                    "Confirm loader, architecture, game build, and companion layout."
                ),
                "fields": {
                    "Binaries": asi_binaries,
                    "Architecture": {
                        path: plugin_details[path].architecture for path in asi_binaries
                    },
                    "Configuration": list(scan.config_files),
                    "CompanionAssets": companion_assets,
                    "DependencyHints": list(scan.dependency_hints),
                    "InstallRoot": "Unresolved; commonly the GTA V root directory",
                },
            }, asi_binaries[0]))
        reshade_binaries = [
            path for path in scan.binary_plugins
            if PurePosixPath(path).suffix.casefold() == ".addon64"
        ]
        if reshade_binaries or scan.shader_assets:
            nodes.append(sourced({
                "id": "reshade.imported", "kind": "reshade_addon",
                "label": "Discovered ReShade companion content",
                "description": (
                    "ReShade add-ons and shaders require an add-on-enabled ReShade "
                    "host and must retain their authored directory layout."
                ),
                "fields": {
                    "Binaries": reshade_binaries,
                    "Architecture": {
                        path: plugin_details[path].architecture
                        for path in reshade_binaries
                    },
                    "Shaders": list(scan.shader_assets),
                    "Configuration": list(scan.config_files),
                    "InstallRoot": "Unresolved; confirm the GTA V/ReShade root layout",
                },
            }, (reshade_binaries or list(scan.shader_assets))[0]))
        if scan.replacement_assets:
            nodes.append(sourced({
                "id": "replacements.imported", "kind": "replacement",
                "label": f"Discovered replacement assets ({len(scan.replacement_assets)})",
                "description": (
                    "Replacement content must be merged into the user's current-build "
                    "archives and cannot be treated as a standalone DLC pack."
                ),
                "fields": {
                    "Assets": list(scan.replacement_assets),
                    "TargetArchives": list(scan.installation_targets) or [
                        "Unresolved; declare every target RPF and entry"
                    ],
                    "Editions": list(scan.edition_hints) or ["Unverified"],
                    "MergeStrategy": "Unresolved; exact-entry merge required",
                    "Backup": "Exact replaced entries plus archive rollback required",
                },
            }, scan.replacement_assets[0]))

        weapon_names = [item.name for item in scan.weapons]
        if scan.weapons:
            weapon_source = scan.weapons[0].source
            nodes.append(sourced({
                "id": "weapons.imported",
                "kind": "weapon",
                "label": f"Discovered weapon records ({len(scan.weapons)})",
                "description": "Fields inferred from XML weapon metadata.",
                "fields": {
                    "Name": weapon_names,
                    "Slot": [item.slot for item in scan.weapons],
                    "AmmoInfo": [item.ammo_info for item in scan.weapons],
                    "Model": [item.model for item in scan.weapons],
                    "HumanNameHash": [item.human_name_hash for item in scan.weapons],
                    "StatName": [item.stat_name for item in scan.weapons],
                },
            }, weapon_source))
        if scan.ammo:
            nodes.append(sourced({
                "id": "ammo.imported",
                "kind": "ammo",
                "label": f"Discovered ammo records ({len(scan.ammo)})",
                "description": "Ammo pools inferred from XML metadata.",
                "fields": {
                    "Name": [item.name for item in scan.ammo],
                    "Model": [item.model for item in scan.ammo],
                    "AmmoMax": [item.ammo_max for item in scan.ammo],
                    "AmmoMax50": [item.ammo_max_50 for item in scan.ammo],
                    "Explosion": [item.explosion for item in scan.ammo],
                    "TrailFx": [item.trail_fx for item in scan.ammo],
                    "PrimedFx": [item.primed_fx for item in scan.ammo],
                },
            }, scan.ammo[0].source))
            if scan.weapons:
                references.append(self._reference(
                    "weapon-ammo", "AmmoInfo", "ammo.imported", "Name",
                    "uses_ammo", "Match each weapon to its declared ammo pool.",
                ))
        if scan.weapon_components:
            nodes.append(sourced({
                "id": "weapon-components.imported",
                "kind": "weapon_component",
                "label": (
                    "Discovered weapon components "
                    f"({len(scan.weapon_components)})"
                ),
                "description": (
                    "Attachment definitions inferred from weapon component metadata."
                ),
                "fields": {
                    "WeaponNames": _unique(
                        item.weapon_name for item in scan.weapon_component_links
                    ),
                    "Names": [item.name for item in scan.weapon_components],
                    "Models": [item.model for item in scan.weapon_components],
                    "AttachBones": [
                        item.attach_bone for item in scan.weapon_components
                    ],
                    "ComponentTypes": [
                        item.component_type for item in scan.weapon_components
                    ],
                },
            }, scan.weapon_components[0].source))
            linked_components = _unique(
                item.component_name for item in scan.weapon_component_links
            )
            if scan.weapons and linked_components:
                references.append({
                    "id": "weapon-components",
                    "source": "weapons.imported",
                    "source_field": "Name",
                    "target": "weapon-components.imported",
                    "target_field": "WeaponNames",
                    "relationship": "offers_components",
                    "description": (
                        "Weapon attachment mappings are retained for specialist "
                        "Workbench review."
                    ),
                    "required": False,
                })
        if scan.animation_weapons:
            source = (
                scan.weapon_animation_records[0].source
                if scan.weapon_animation_records else next((
                    entry.path for entry in scan.entries
                    if "weaponanimation" in entry.path.lower()
                ), None)
            )
            nodes.append(sourced({
                "id": "animations.imported", "kind": "animation",
                "label": "Discovered animation mappings",
                "fields": {
                    "WeaponNames": list(scan.animation_weapons),
                    "Template": "Verify the native animation template",
                    "Sets": ["Detected XML mappings; inspect every required set"],
                },
            }, source))
            if scan.weapons:
                references.append(self._reference(
                    "weapon-animation", "Name", "animations.imported",
                    "WeaponNames", "uses_animation",
                    "Require animation coverage for every discovered weapon.",
                ))
        if scan.shop_weapons:
            source = (
                scan.weapon_shop_records[0].source
                if scan.weapon_shop_records else next((
                    entry.path for entry in scan.entries
                    if "weapon_shop" in entry.path.lower()
                ), None)
            )
            nodes.append(sourced({
                "id": "storefront.imported", "kind": "storefront",
                "label": "Discovered weapon shop registration",
                "fields": {
                    "WeaponNames": list(scan.shop_weapons),
                    "Catalog": "weapon_shop.meta",
                    "Persistence": "Declare purchase and save behavior",
                },
            }, source))
            if scan.weapons:
                references.append(self._reference(
                    "weapon-storefront", "Name", "storefront.imported",
                    "WeaponNames", "sold_by",
                    "Match discovered weapons to shop registrations.",
                ))

        vehicle_names = [item.model_name for item in scan.vehicles]
        variation_kits = {
            item.model_name.casefold(): list(item.kits) for item in scan.variations
        }
        available_kit_names = {item.name.casefold() for item in scan.kits}
        declared_tuning_models = _unique(
            variation.model_name for variation in scan.variations
            if any(
                kit.casefold() in available_kit_names for kit in variation.kits
            )
        )
        if scan.vehicles:
            nodes.append(sourced({
                "id": "vehicles.imported", "kind": "vehicle",
                "label": f"Discovered vehicle records ({len(scan.vehicles)})",
                "description": "Vehicle definitions inferred from vehicles.meta.",
                "fields": {
                    "ModelName": vehicle_names,
                    "TxdName": [item.txd_name for item in scan.vehicles],
                    "HandlingId": [item.handling_id for item in scan.vehicles],
                    "GameName": [item.game_name for item in scan.vehicles],
                    "MakeName": [item.make_name for item in scan.vehicles],
                    "AudioNameHash": [item.audio_name_hash for item in scan.vehicles],
                    "Layout": [item.layout for item in scan.vehicles],
                    "Type": [item.vehicle_type for item in scan.vehicles],
                    "Class": [item.vehicle_class for item in scan.vehicles],
                    "Editions": [item.edition or "unresolved" for item in scan.vehicles],
                    "DefinitionSources": [item.source for item in scan.vehicles],
                    "TuningKits": _unique(
                        kit for vehicle in scan.vehicles
                        for kit in variation_kits.get(vehicle.model_name.casefold(), [])
                    ),
                    "TuningModels": declared_tuning_models,
                },
            }, scan.vehicles[0].source))
        if scan.handlings:
            nodes.append(sourced({
                "id": "handling.imported", "kind": "handling",
                "label": f"Discovered handling records ({len(scan.handlings)})",
                "fields": {
                    "HandlingNames": [item.name for item in scan.handlings],
                },
            }, scan.handlings[0].source))
            if scan.vehicles:
                references.append(self._reference(
                    "vehicle-handling", "HandlingId", "handling.imported",
                    "HandlingNames", "uses_handling",
                    "Match every vehicle handlingId to handling.meta.",
                    source="vehicles.imported",
                ))
        if scan.variations:
            nodes.append(sourced({
                "id": "variations.imported", "kind": "vehicle_variation",
                "label": f"Discovered vehicle variations ({len(scan.variations)})",
                "fields": {
                    "ModelNames": [item.model_name for item in scan.variations],
                    "Kits": _unique(
                        kit for item in scan.variations for kit in item.kits
                    ),
                    "LightSettings": [
                        item.light_settings for item in scan.variations
                    ],
                },
            }, scan.variations[0].source))
            if scan.vehicles:
                references.append(self._reference(
                    "vehicle-variation", "ModelName", "variations.imported",
                    "ModelNames", "uses_variation",
                    "Require carvariations coverage for every vehicle model.",
                    source="vehicles.imported",
                ))
        if scan.kits:
            tuned_models = declared_tuning_models
            nodes.append(sourced({
                "id": "tuning.imported", "kind": "tuning",
                "label": f"Discovered tuning kits ({len(scan.kits)})",
                "fields": {
                    "VehicleModels": tuned_models,
                    "KitNames": [item.name for item in scan.kits],
                    "ModelNames": _unique(
                        model for item in scan.kits for model in item.model_names
                    ),
                    "KitIds": [item.kit_id for item in scan.kits],
                },
            }, scan.kits[0].source))
            if scan.vehicles and tuned_models:
                references.append(self._reference(
                    "vehicle-tuning", "TuningModels", "tuning.imported",
                    "VehicleModels", "uses_tuning",
                    "Match vehicles with declared mod kits to carcols definitions.",
                    source="vehicles.imported",
                ))

        if scan.peds:
            nodes.append(sourced({
                "id": "peds.imported", "kind": "ped",
                "label": f"Discovered ped records ({len(scan.peds)})",
                "description": "Ped definitions inferred from peds.meta.",
                "fields": {
                    "Names": [item.name for item in scan.peds],
                    "PedTypes": [item.ped_type for item in scan.peds],
                    "ModelTypes": [item.model_type for item in scan.peds],
                    "PropsNames": [item.props_name for item in scan.peds],
                    "ClipDictionaries": [
                        item.clip_dictionary for item in scan.peds
                    ],
                    "ExpressionSets": [
                        item.expression_set for item in scan.peds
                    ],
                    "MovementClipSets": [
                        item.movement_clip_set for item in scan.peds
                    ],
                    "CreatureMetadata": [
                        item.creature_metadata for item in scan.peds
                    ],
                },
            }, scan.peds[0].source))

        nested_streamed_assets = [
            self._virtual_asset_path(item) for item in scan.rpf_native_assets
            if item.suffix in {".ydr", ".ydd", ".yft", ".ytd"}
        ]
        streamed_models = _unique_casefold([
            *(
                PurePosixPath(entry.path).stem for entry in scan.entries
                if entry.suffix in {".ydr", ".ydd", ".yft"}
            ),
            *(
                PurePosixPath(entry.path).stem for entry in scan.rpf_native_assets
                if entry.suffix in {".ydr", ".ydd", ".yft"}
            ),
        ])
        streamed_textures = _unique_casefold([
            *(
                PurePosixPath(entry.path).stem for entry in scan.entries
                if entry.suffix == ".ytd"
            ),
            *(
                PurePosixPath(entry.path).stem for entry in scan.rpf_native_assets
                if entry.suffix == ".ytd"
            ),
        ])
        streamed_assets = [
            entry.path for entry in scan.entries
            if entry.suffix in {".ydr", ".ydd", ".yft", ".ytd"}
        ] + nested_streamed_assets
        if streamed_assets and (scan.vehicles or scan.kits or scan.peds):
            nodes.append({
                "id": "streaming.imported", "kind": "streaming",
                "label": f"Discovered streamed assets ({len(streamed_assets)})",
                "fields": {
                    "ModelNames": streamed_models,
                    "TextureNames": streamed_textures,
                    "Assets": streamed_assets,
                },
            })
            if scan.vehicles:
                references.append(self._reference(
                    "vehicle-stream", "ModelName", "streaming.imported",
                    "ModelNames", "streams_model",
                    "Require a streamed model for every vehicle definition.",
                    source="vehicles.imported",
                ))
            if scan.kits:
                references.append(self._reference(
                    "tuning-stream", "ModelNames", "streaming.imported",
                    "ModelNames", "streams_tuning_assets",
                    "Require every tuning model referenced by carcols.meta.",
                    source="tuning.imported",
                ))
            if scan.peds:
                references.append({
                    "id": "ped-stream",
                    "source": "peds.imported",
                    "source_field": "Names",
                    "target": "streaming.imported",
                    "target_field": "ModelNames",
                    "relationship": "streams_ped_assets",
                    "description": (
                        "Associate ped definitions with their streamed drawable "
                        "and texture dictionaries."
                    ),
                    "required": False,
                })

        if scan.registrations:
            registration_sources = [item.source for item in scan.registrations]
            nodes.append(sourced({
                "id": "registration.imported", "kind": "dlc_registration",
                "label": "Discovered package registration",
                "fields": {
                    "VehicleModels": vehicle_names,
                    "PackageNames": _unique(
                        name for item in scan.registrations
                        for name in item.package_names
                    ),
                    "MetadataFiles": _unique(
                        name for item in scan.registrations
                        for name in item.metadata_files
                    ),
                    "Registration": _unique(
                        item.kind for item in scan.registrations
                    ),
                    "Edition": "Verify registration against the selected GTA edition",
                },
            }, registration_sources[0]))
            if scan.vehicles:
                references.append(self._reference(
                    "vehicle-registration", "ModelName", "registration.imported",
                    "VehicleModels", "registered_by",
                    "Tie every vehicle to an explicit DLC/resource registration.",
                    source="vehicles.imported",
                ))

        inspected_archives = {
            item.source.casefold(): item for item in scan.rpf_archives
        }
        for index, entry in enumerate(
            (item for item in scan.entries if item.suffix == ".rpf"), start=1
        ):
            if index > 20:
                break
            inspected = inspected_archives.get(entry.path.casefold())
            nodes.append(sourced({
                "id": f"archive.imported-{index}", "kind": "archive",
                "label": (
                    f"Inspected RPF: {entry.path}" if inspected
                    else f"Unresolved RPF: {entry.path}"
                ),
                "fields": {
                    "Path": entry.path,
                    "Inspection": (
                        "Recursive read-only index complete" if inspected
                        else "Recursive inspection required"
                    ),
                    "Edition": inspected.edition if inspected else "unresolved",
                    "ArchiveLayers": inspected.archive_count if inspected else 0,
                    "Entries": inspected.entry_count if inspected else 0,
                    "AssetTypes": inspected.suffix_counts if inspected else {},
                    "MergeStrategy": (
                        "Edition-specific DLC archive; managed conversion required"
                    ),
                    "Backup": "Exact originals and rollback plan required",
                },
            }, entry.path))

        steps: list[dict[str, Any]] = []
        categories = (
            ("metadata", lambda item: item.suffix in XML_SUFFIXES,
             "metadata review/merge"),
            ("assets", lambda item: item.suffix in ASSET_SUFFIXES,
             "streamed asset validation"),
            ("archives", lambda item: item.suffix == ".rpf",
             "nested archive inspection"),
            ("scripts", lambda item: item.suffix in {".dll", ".asi", ".cs", ".lua"},
             "runtime dependency review"),
            ("shaders", lambda item: item.suffix in {".addon64", ".fx", ".fxh"},
             "shader host and directory-layout review"),
        )
        order = 10
        for category, predicate, strategy in categories:
            matches = [entry for entry in scan.entries if predicate(entry)]
            if not matches:
                continue
            step: dict[str, Any] = {
                "id": f"inspect-{category}", "order": order,
                "title": f"Inspect {category}",
                "target": ", ".join(item.path for item in matches[:8]) +
                    (f" (+{len(matches) - 8} more)" if len(matches) > 8 else ""),
                "strategy": strategy,
                "description": (
                    "Generated inventory step. Replace this with an exact target, "
                    "merge rule, verifier, and rollback action."
                ),
            }
            if folder_sources and len(matches) == 1:
                step["source"] = matches[0].path
            steps.append(step)
            order += 10
        steps.append({
            "id": "verify-and-rollback", "order": 90,
            "title": "Verify and define rollback",
            "target": "Installed package",
            "strategy": "read-only verification before transactional install",
            "description": (
                "Resolve every linker error, test the intended game edition, and "
                "define exact backups before enabling installation."
            ),
        })

        manifest = {
            "schema_version": 1,
            "id": f"imported.{_slug(scan.source.stem)}",
            "name": f"Imported draft: {scan.source.stem.replace('_', ' ').title()}",
            "version": "0.1.0-draft",
            "summary": (
                f"Read-only draft generated from {len(scan.entries)} files. "
                f"Importer findings: {scan.error_count} errors and "
                f"{scan.warning_count} warnings."
            ),
            "editions": list(scan.edition_hints) or ["legacy", "enhanced"],
            "nodes": nodes,
            "references": references,
            "install_steps": steps,
        }
        return ImportedAddonDraft(scan, manifest)

    @staticmethod
    def _virtual_asset_path(item: RpfNativeEntryRecord) -> str:
        segments = [item.source]
        if item.archive_path:
            segments.append(item.archive_path)
        segments.append(item.path)
        return "!".join(segments)

    @staticmethod
    def _reference(
        reference_id: str, source_field: str, target: str,
        target_field: str, relationship: str, description: str,
        *, source: str = "weapons.imported",
    ) -> dict[str, Any]:
        return {
            "id": reference_id, "source": source,
            "source_field": source_field, "target": target,
            "target_field": target_field, "relationship": relationship,
            "description": description,
        }
