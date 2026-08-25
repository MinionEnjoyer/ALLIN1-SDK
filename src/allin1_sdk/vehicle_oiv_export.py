"""Deterministic Legacy OIV export for validated vehicle add-on packages.

This exporter intentionally carries only the DLC vehicle archive and its
``dlclist.xml`` registration.  ALLIN1 GBAY metadata, traffic preferences,
receipts, backups, and managed rollback are Launcher features and are not
represented by this compatibility package.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from lxml import etree as ET

from allin1_sdk.addon_importer import PackageAssetReader
from allin1_sdk.managed_package_conversion import (
    MAX_CONVERTED_RPF_BYTES,
    ManagedVehiclePackagePlan,
)
from allin1_sdk.mods import ModManifest


_PACKAGE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_DLC_PACK_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_OIV_FORMAT = "2.2"
_CHUNK_SIZE = 1024 * 1024
_FIXED_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_id(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Package id must be a string")
    normalized = value.strip().casefold()
    if not _PACKAGE_ID_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Package id must be 2-64 lowercase letters, numbers, dots, "
            "dashes, or underscores"
        )
    if normalized.startswith("allin1."):
        raise ValueError("The allin1.* package namespace is reserved")
    return normalized


def _validated_pack(value: object) -> str:
    if not isinstance(value, str) or not _DLC_PACK_PATTERN.fullmatch(value):
        raise ValueError("DLC pack name is unsafe for an OIV content path")
    return value


def _validated_text(value: object, label: str, *, limit: int = 200) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = value.strip()
    if (
        not normalized or len(normalized) > limit
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ValueError(f"{label} must contain 1-{limit} printable characters")
    return normalized


def _version_parts(version: str) -> tuple[str, str, str]:
    match = re.fullmatch(
        r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+](.+))?",
        version.strip(),
    )
    if match:
        patch = match.group(3)
        suffix = match.group(4) or ""
        tags = []
        if patch is not None and int(patch) != 0:
            tags.append(f"Patch {int(patch)}")
        if suffix:
            tags.append(suffix)
        return match.group(1), match.group(2) or "0", " ".join(tags)
    return "1", "0", version.strip()


def _is_relative_to(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class LegacyVehicleOivSource:
    """Exact Legacy payload authorized for compatibility export."""

    package_id: str
    name: str
    version: str
    dlc_pack: str
    payload_path: Path
    payload_size: int
    payload_sha256: str
    source_label: str
    source_root: Path | None = None


@dataclass(frozen=True)
class LegacyVehicleOivResult:
    """Typed evidence for a completed no-game-write OIV export."""

    archive: Path
    archive_size: int
    archive_sha256: str
    package_id: str
    dlc_pack: str
    payload_size: int
    payload_sha256: str
    assembly_sha256: str
    members: tuple[str, ...]
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": "export_legacy_vehicle_oiv",
            "authoring_write": True,
            "game_write_performed": False,
            "archive": str(self.archive),
            "archive_size": self.archive_size,
            "archive_sha256": self.archive_sha256,
            "package_id": self.package_id,
            "dlc_pack": self.dlc_pack,
            "payload_size": self.payload_size,
            "payload_sha256": self.payload_sha256,
            "assembly_sha256": self.assembly_sha256,
            "members": list(self.members),
            "source": self.source,
            "compatibility": {
                "edition": "legacy",
                "installs_vehicle_files": True,
                "registers_dlclist": True,
                "includes_gbay_catalog": False,
                "includes_traffic_preference": False,
                "includes_allin1_receipt": False,
                "includes_managed_backup_or_rollback": False,
                "notice": (
                    "This OIV installs the Legacy DLC vehicle files only. Use the "
                    "ALLIN1 package for GBAY, traffic controls, receipts, backups, "
                    "and managed rollback."
                ),
            },
        }


class LegacyVehicleOivExporter:
    """Create conventional Legacy vehicle OIVs without writing GTA V."""

    def __init__(self, gta_path: str | Path | None = None) -> None:
        self.gta_path = (
            Path(gta_path).expanduser().resolve(strict=False)
            if gta_path is not None else None
        )

    def export_plan(
        self,
        plan: ManagedVehiclePackagePlan,
        destination: str | Path,
        *,
        author: str,
    ) -> LegacyVehicleOivResult:
        """Export the exact hash-bound RPF selected by a validated plan."""
        if plan.edition.casefold() != "legacy":
            raise ValueError(
                "OIV compatibility export supports Legacy vehicle packages only; "
                "Enhanced packages must remain in the ALLIN1 managed format"
            )
        package_id = _validated_id(plan.package_id)
        dlc_pack = _validated_pack(plan.dlc_pack)
        expected_destination = (
            f"mods/update/x64/dlcpacks/{dlc_pack}/dlc.rpf"
        )
        if plan.destination.casefold() != expected_destination.casefold():
            raise ValueError("Managed vehicle plan has an unexpected DLC destination")
        if (
            not isinstance(plan.source_member_sha256, str)
            or not _SHA256_PATTERN.fullmatch(plan.source_member_sha256)
            or plan.source_member_size <= 0
            or plan.source_member_size > MAX_CONVERTED_RPF_BYTES
        ):
            raise ValueError("Managed vehicle plan has invalid payload evidence")
        if not plan.vehicles or not plan.registration_sources or not any(
            value.casefold().removeprefix("dlc_") == dlc_pack.casefold()
            for value in plan.registered_package_names
        ):
            raise ValueError(
                "Managed vehicle plan lacks vehicle or DLC registration evidence"
            )
        if plan.catalog.catalog_id != package_id:
            raise ValueError("Managed vehicle plan catalog id does not match its package")
        plan.catalog.validate_package_ownership((dlc_pack,), allow_traffic=True)
        if plan.source.is_file():
            if (
                not isinstance(plan.source_package_sha256, str)
                or not _SHA256_PATTERN.fullmatch(plan.source_package_sha256)
                or _sha256_file(plan.source) != plan.source_package_sha256
            ):
                raise ValueError(
                    "Source package changed after the managed vehicle plan was made"
                )
        elif plan.source_package_sha256 is not None:
            raise ValueError("Folder source plan has unexpected package hash evidence")

        reader = PackageAssetReader(plan.source)
        asset = reader.read(plan.source_member, limit=plan.source_member_size + 1)
        if (
            asset.truncated or len(asset.data) != plan.source_member_size
            or asset.sha256 != plan.source_member_sha256
        ):
            raise ValueError(
                "Source dlc.rpf changed after the managed vehicle plan was made"
            )
        return self._write(
            LegacyVehicleOivSource(
                package_id=package_id,
                name=_validated_text(plan.name, "Package name"),
                version=_validated_text(plan.version, "Package version", limit=80),
                dlc_pack=dlc_pack,
                payload_path=plan.source,
                payload_size=plan.source_member_size,
                payload_sha256=plan.source_member_sha256,
                source_label=f"{plan.source.name}!{plan.source_member}",
                source_root=plan.source if plan.source.is_dir() else None,
            ),
            destination,
            author=author,
            payload_bytes=asset.data,
        )

    def export_prepared(
        self,
        package_root: str | Path,
        destination: str | Path,
        *,
        author: str,
    ) -> LegacyVehicleOivResult:
        """Export one exact SDK-prepared schema-2 Legacy vehicle package."""
        root = Path(package_root).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Prepared vehicle package must be a folder")
        manifest = ModManifest.load(root / "mod.toml")
        if manifest.schema_version != 2 or manifest.extension is None:
            raise ValueError("OIV export requires a validated schema-2 ALLIN1 package")
        if manifest.editions != ("legacy",):
            raise ValueError(
                "OIV compatibility export supports Legacy vehicle packages only; "
                "Enhanced packages must remain in the ALLIN1 managed format"
            )
        if manifest.mod_type != "mixed" or len(manifest.dlc_packs) != 1:
            raise ValueError("Prepared package is not one managed vehicle DLC pack")
        if not any(
            catalog.kind == "vehicle"
            for catalog in manifest.extension.gbay_catalogs
        ):
            raise ValueError("Prepared package does not declare a GBAY vehicle catalog")
        package_id = _validated_id(manifest.mod_id)
        dlc_pack = _validated_pack(manifest.dlc_packs[0])
        expected_destination = (
            f"mods/update/x64/dlcpacks/{dlc_pack}/dlc.rpf"
        )
        rpf_files = [
            item for item in manifest.files
            if item.destination.as_posix().casefold()
            == expected_destination.casefold()
        ]
        if len(rpf_files) != 1 or rpf_files[0].sha256 is None:
            raise ValueError(
                "Prepared package must own one exact hash-bound DLC RPF payload"
            )
        item = rpf_files[0]
        payload = root / Path(*item.source.parts)
        if payload.is_symlink() or not payload.is_file():
            raise ValueError("Prepared package DLC RPF payload is missing or unsafe")
        payload = payload.resolve(strict=True)
        if not _is_relative_to(payload, root):
            raise ValueError("Prepared package payload escapes its package root")

        review_path = root / "allin1.review.json"
        if review_path.is_symlink() or not review_path.is_file():
            raise ValueError("Prepared package review evidence is missing or unsafe")
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Prepared package review evidence is invalid: {exc}") from exc
        expected_review = {
            "schema_version": 1,
            "operation": "managed_vehicle_package_conversion",
            "review_only": True,
            "install_performed": False,
            "package_id": package_id,
            "edition": "legacy",
            "dlc_pack": dlc_pack,
            "source_member_sha256": item.sha256,
        }
        if not isinstance(review, dict) or any(
            review.get(key) != value for key, value in expected_review.items()
        ):
            raise ValueError(
                "Prepared package review evidence does not match its manifest"
            )

        return self._write(
            LegacyVehicleOivSource(
                package_id=package_id,
                name=_validated_text(manifest.name, "Package name"),
                version=_validated_text(
                    manifest.version, "Package version", limit=80,
                ),
                dlc_pack=dlc_pack,
                payload_path=payload,
                payload_size=payload.stat().st_size,
                payload_sha256=item.sha256,
                source_label=root.name,
                source_root=root,
            ),
            destination,
            author=author,
        )

    def _write(
        self,
        source: LegacyVehicleOivSource,
        destination: str | Path,
        *,
        author: str,
        payload_bytes: bytes | None = None,
    ) -> LegacyVehicleOivResult:
        output = Path(destination).expanduser().resolve(strict=False)
        if output.suffix.casefold() != ".oiv":
            raise ValueError("OIV export destination must use an .oiv filename")
        if output.exists() or output.is_symlink():
            raise ValueError(f"OIV export destination already exists: {output}")
        if self.gta_path is not None and _is_relative_to(output, self.gta_path):
            raise ValueError("OIV packages may not be exported inside GTA V")
        if source.source_root is not None and _is_relative_to(
            output, source.source_root.resolve(strict=False),
        ):
            raise ValueError("OIV packages may not be exported inside their source")
        output.parent.mkdir(parents=True, exist_ok=True)
        author = _validated_text(author, "Author")
        if (
            source.payload_size <= 0
            or source.payload_size > MAX_CONVERTED_RPF_BYTES
            or not _SHA256_PATTERN.fullmatch(source.payload_sha256)
        ):
            raise ValueError("OIV source has invalid payload evidence")
        if payload_bytes is not None:
            if (
                len(payload_bytes) != source.payload_size
                or hashlib.sha256(payload_bytes).hexdigest() != source.payload_sha256
            ):
                raise ValueError("In-memory OIV payload does not match its evidence")
        elif (
            source.payload_path.stat().st_size != source.payload_size
            or _sha256_file(source.payload_path) != source.payload_sha256
        ):
            raise ValueError("Prepared dlc.rpf changed before OIV export")

        assembly = self._assembly_xml(source, author)
        assembly_sha256 = hashlib.sha256(assembly).hexdigest()
        payload_member = f"content/dlcpacks/{source.dlc_pack}/dlc.rpf"
        members = ("assembly.xml", payload_member)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.stem}-", suffix=".oiv", dir=output.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name).resolve()
        try:
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True,
            ) as archive:
                self._write_member(archive, "assembly.xml", assembly)
                info = self._member_info(payload_member, source.payload_size)
                with archive.open(
                    info, "w", force_zip64=source.payload_size >= 2 * 1024**3,
                ) as target:
                    if payload_bytes is not None:
                        target.write(payload_bytes)
                    else:
                        with source.payload_path.open("rb") as payload:
                            self._copy(payload, target)

            with zipfile.ZipFile(temporary) as archive:
                if tuple(item.filename for item in archive.infolist()) != members:
                    raise RuntimeError("OIV member verification failed")
                if archive.testzip() is not None:
                    raise RuntimeError("OIV ZIP integrity verification failed")
                if hashlib.sha256(archive.read("assembly.xml")).hexdigest() != (
                    assembly_sha256
                ):
                    raise RuntimeError("OIV assembly.xml verification failed")
                payload_digest = hashlib.sha256()
                payload_size = 0
                with archive.open(payload_member) as payload:
                    for chunk in iter(lambda: payload.read(_CHUNK_SIZE), b""):
                        payload_digest.update(chunk)
                        payload_size += len(chunk)
                if (
                    payload_size != source.payload_size
                    or payload_digest.hexdigest() != source.payload_sha256
                ):
                    raise RuntimeError("OIV payload verification failed")
            archive_size = temporary.stat().st_size
            archive_sha256 = _sha256_file(temporary)
            # Claim the final name exclusively so a concurrent export is never
            # overwritten. Replacing our own zero-byte claim remains atomic.
            try:
                descriptor = os.open(
                    output, os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError as exc:
                raise ValueError(
                    f"OIV export destination already exists: {output}"
                ) from exc
            os.close(descriptor)
            try:
                temporary.replace(output)
            except Exception:
                output.unlink(missing_ok=True)
                raise
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        return LegacyVehicleOivResult(
            archive=output,
            archive_size=archive_size,
            archive_sha256=archive_sha256,
            package_id=source.package_id,
            dlc_pack=source.dlc_pack,
            payload_size=source.payload_size,
            payload_sha256=source.payload_sha256,
            assembly_sha256=assembly_sha256,
            members=members,
            source=source.source_label,
        )

    @staticmethod
    def _assembly_xml(source: LegacyVehicleOivSource, author: str) -> bytes:
        package_guid = uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                f"https://allin1.invalid/oiv/{source.package_id}/"
                f"{source.version}/{source.payload_sha256}"
            ),
        )
        root = ET.Element("package", {
            "version": _OIV_FORMAT,
            "id": "{" + str(package_guid).upper() + "}",
            "target": "Five",
        })
        metadata = ET.SubElement(root, "metadata")
        ET.SubElement(metadata, "name").text = source.name
        version = ET.SubElement(metadata, "version")
        major, minor, tag = _version_parts(source.version)
        ET.SubElement(version, "major").text = major
        ET.SubElement(version, "minor").text = minor
        if tag:
            ET.SubElement(version, "tag").text = tag
        author_node = ET.SubElement(metadata, "author")
        ET.SubElement(author_node, "displayName").text = author
        description = (
            "Installs this Legacy DLC vehicle archive and registers its DLC pack. "
            "GBAY listings, traffic preferences, ALLIN1 receipts, backups, and "
            "managed rollback are available only through the ALLIN1 package."
        )
        ET.SubElement(metadata, "description").text = ET.CDATA(description)
        ET.SubElement(metadata, "largeDescription").text = ET.CDATA(description)

        colors = ET.SubElement(root, "colors")
        header = ET.SubElement(
            colors, "headerBackground", {"useBlackTextColor": "False"},
        )
        header.text = "$FF2D9C50"
        ET.SubElement(colors, "iconBackground").text = "$FF1F7F42"

        content = ET.SubElement(root, "content")
        add = ET.SubElement(
            content, "add",
            {"source": f"dlcpacks/{source.dlc_pack}/dlc.rpf"},
        )
        # OIV destinations are rooted at the game folder. The installer owns
        # the user's explicit choice to route those targets through a mods
        # folder, so the recipe itself must not hard-code that policy.
        add.text = f"update\\x64\\dlcpacks\\{source.dlc_pack}\\dlc.rpf"
        archive = ET.SubElement(content, "archive", {
            "path": "update\\update.rpf",
            "createIfNotExist": "False",
            "type": "RPF7",
        })
        xml = ET.SubElement(
            archive, "xml", {"path": "common\\data\\dlclist.xml"},
        )
        insert = ET.SubElement(
            xml, "add",
            {"xpath": "/SMandatoryPacksData/Paths", "append": "Last"},
        )
        ET.SubElement(insert, "Item").text = f"dlcpacks:/{source.dlc_pack}/"
        ET.indent(root, space="  ")
        data = ET.tostring(
            root, encoding="utf-8", xml_declaration=True, pretty_print=False,
        )
        # Parse our own output and reject any accidental declaration drift.
        parsed = ET.fromstring(data)
        if (
            parsed.tag != "package"
            or parsed.attrib.get("version") != _OIV_FORMAT
            or parsed.attrib.get("target") != "Five"
            or not re.fullmatch(
                r"\{[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}\}",
                parsed.attrib.get("id", ""),
            )
            or parsed.find("metadata") is None
            or parsed.find("colors") is None
            or parsed.find("content") is None
        ):
            raise RuntimeError("Generated OIV assembly.xml failed verification")
        return data

    @staticmethod
    def _member_info(name: str, size: int) -> zipfile.ZipInfo:
        # Member names are authored internally from a validated DLC identifier.
        normalized = PurePosixPath(name).as_posix()
        info = zipfile.ZipInfo(normalized, date_time=_FIXED_ZIP_DATE)
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_STORED
        info.file_size = size
        return info

    @classmethod
    def _write_member(
        cls, archive: zipfile.ZipFile, name: str, payload: bytes,
    ) -> None:
        archive.writestr(cls._member_info(name, len(payload)), payload)

    @staticmethod
    def _copy(source: BinaryIO, target: BinaryIO) -> None:
        for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
            target.write(chunk)
