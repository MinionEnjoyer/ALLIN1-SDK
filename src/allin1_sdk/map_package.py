"""Guarded publication of map DLC assets and declarative runtime metadata."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from allin1_sdk.addon_importer import (
    AddonPackageInspector,
    PackageAssetReader,
    PackageEntry,
    PackageScan,
)
from allin1_sdk.map_contract import MapProject, SUPPORTED_EDITIONS
from allin1_sdk.map_project import MapProjectResolver
from allin1_sdk.mods import ModManifest
from allin1_sdk.rpf_builder import RpfArchiveBuilder


MAX_IMPORTED_MAP_RPF_BYTES = 1024 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class MapPackageResult:
    root: Path
    manifest: Path
    payload: Path
    descriptor: Path
    content_manifest: Path
    report: Path
    pack_name: str
    package_id: str
    edition: str
    payload_sha256: str
    descriptor_sha256: str
    source_mode: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "root": str(self.root),
            "manifest": str(self.manifest),
            "payload": str(self.payload),
            "descriptor": str(self.descriptor),
            "content_manifest": str(self.content_manifest),
            "report": str(self.report),
            "pack_name": self.pack_name,
            "package_id": self.package_id,
            "edition": self.edition,
            "payload_sha256": self.payload_sha256,
            "descriptor_sha256": self.descriptor_sha256,
            "source_mode": self.source_mode,
        }


class MapAddonPackageBuilder:
    """Build a new, single-edition ALLIN1 map package without touching its source."""

    def __init__(
        self, project_root: str | Path, gta_path: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.gta_path = (
            Path(gta_path).expanduser().resolve() if gta_path is not None else None
        )

    def build(
        self,
        source: str | Path,
        descriptor_or_path: MapProject | Mapping[str, Any] | str | Path,
        destination: str | Path,
        *,
        edition: str,
    ) -> MapPackageResult:
        source_path = Path(source).expanduser().resolve()
        if not source_path.exists():
            raise FileNotFoundError(f"Map source was not found: {source_path}")
        descriptor = self._load_descriptor(descriptor_or_path)
        if descriptor.package_id.startswith("allin1."):
            raise ValueError("The allin1.* package namespace is reserved")
        selected_edition = edition.strip().casefold()
        if selected_edition not in SUPPORTED_EDITIONS:
            raise ValueError("Map package edition must be legacy or enhanced")
        if selected_edition not in descriptor.editions:
            raise ValueError(
                f"Map project does not declare support for {selected_edition.title()}"
            )

        target = Path(destination).expanduser().resolve()
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Map package destination already exists: {target}")
        if source_path.is_dir() and (target == source_path or target.is_relative_to(source_path)):
            raise ValueError("Map package output must be outside its source tree")
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(
            prefix=f".{target.name}.map-package-", dir=target.parent,
        )).resolve()

        payload_digest = ""
        descriptor_digest = ""
        source_mode = ""
        try:
            scan = AddonPackageInspector(
                self.project_root, self.gta_path,
            ).inspect(source_path)
            inspection = MapProjectResolver.inspect_scan(scan)
            if not any(item.role == "placement" for item in inspection.assets):
                raise ValueError(
                    "Map source does not expose a YMAP placement asset for validation"
                )

            payload = stage / "payload" / "dlc.rpf"
            payload.parent.mkdir(parents=True)
            source_mode, source_evidence, builder_validation = self._materialize_rpf(
                source_path, scan, payload, descriptor,
            )
            payload_digest = _sha256(payload)

            descriptor_path = stage / "payload" / "maps.json"
            descriptor_path.write_text(
                json.dumps(descriptor.to_dict(), indent=2) + "\n", encoding="utf-8",
            )
            descriptor_digest = _sha256(descriptor_path)

            content_path = stage / "allin1.content.json"
            content_path.write_text(
                json.dumps(self._content_manifest(descriptor), indent=2) + "\n",
                encoding="utf-8",
            )
            manifest_path = stage / "mod.toml"
            manifest_path.write_text(
                self._manifest_text(
                    descriptor, selected_edition, payload_digest, descriptor_digest,
                ),
                encoding="utf-8",
            )
            inspection_payload = inspection.to_dict()
            inspection_payload["source"] = self._portable_source_label(source_path)
            report_payload = {
                "schema_version": 1,
                "operation": "map_addon_package_build",
                "status": "validated",
                "source": self._portable_source_label(source_path),
                "source_mode": source_mode,
                "source_evidence": source_evidence,
                "package_id": descriptor.package_id,
                "project_id": descriptor.project_id,
                "edition": selected_edition,
                "pack_name": descriptor.streaming.pack_name,
                "payload": {
                    "path": "payload/dlc.rpf",
                    "size": payload.stat().st_size,
                    "sha256": payload_digest,
                    "builder_validation": builder_validation,
                },
                "descriptor": {
                    "path": "payload/maps.json",
                    "sha256": descriptor_digest,
                    "project": descriptor.to_dict(),
                },
                "map_project": inspection_payload,
                "safety": {
                    "source_unchanged": True,
                    "output_was_new": True,
                    "stock_game_files_modified": False,
                    "single_edition_target": True,
                    "manifest_payload_validated": True,
                },
            }
            report_path = stage / "map-package-report.json"
            report_path.write_text(
                json.dumps(report_payload, indent=2) + "\n", encoding="utf-8",
            )
            # This validates schema parity, checksums, package ownership, the
            # content descriptor, and exact DLC registration destinations.
            ModManifest.load(manifest_path)
            stage.rename(target)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

        return MapPackageResult(
            root=target,
            manifest=target / "mod.toml",
            payload=target / "payload" / "dlc.rpf",
            descriptor=target / "payload" / "maps.json",
            content_manifest=target / "allin1.content.json",
            report=target / "map-package-report.json",
            pack_name=descriptor.streaming.pack_name,
            package_id=descriptor.package_id,
            edition=selected_edition,
            payload_sha256=payload_digest,
            descriptor_sha256=descriptor_digest,
            source_mode=source_mode,
        )

    @staticmethod
    def _load_descriptor(
        value: MapProject | Mapping[str, Any] | str | Path,
    ) -> MapProject:
        if isinstance(value, MapProject):
            # Round-trip through the parser so callers cannot bypass validation
            # by constructing dataclasses manually.
            return MapProject.from_dict(value.to_dict())
        if isinstance(value, Mapping):
            return MapProject.from_dict(dict(value))
        return MapProject.load(value)

    def _materialize_rpf(
        self,
        source: Path,
        scan: PackageScan,
        destination: Path,
        descriptor: MapProject,
    ) -> tuple[str, dict[str, Any], str | None]:
        if source.is_file() and source.suffix.casefold() == ".rpf":
            if source.is_symlink():
                raise ValueError("Direct RPF sources may not be symbolic links")
            if source.stat().st_size > MAX_IMPORTED_MAP_RPF_BYTES:
                raise ValueError("Map RPF exceeds the guarded 1 GiB package import limit")
            self._require_indexed_placement(scan)
            self._require_declared_ipls(
                descriptor,
                self._indexed_ymap_names(scan),
                self._portable_source_label(source),
            )
            shutil.copyfile(source, destination)
            return "direct_rpf", {
                "source": self._portable_source_label(source),
                "size": source.stat().st_size,
            }, None

        members = tuple(
            entry for entry in scan.entries
            if PurePosixPath(entry.path).name.casefold() == "dlc.rpf"
        )
        if len(members) > 1:
            raise ValueError(
                "Map package contains multiple dlc.rpf payloads; select one DLC pack"
            )
        if members:
            member = members[0]
            self._require_indexed_placement(scan, archive_path=member.path)
            self._require_declared_ipls(
                descriptor,
                self._indexed_ymap_names(scan, archive_path=member.path),
                member.path,
            )
            self._copy_member(source, member, destination)
            return "prebuilt_dlc_rpf", {
                "member": member.path, "size": member.size,
            }, None

        rpf_source = self._find_rpf_source(source)
        if rpf_source is None:
            raise ValueError(
                "No direct RPF, dlc.rpf payload, or dlc.rpf.source authoring "
                "directory was found"
            )
        if self.gta_path is None:
            raise ValueError(
                "Building dlc.rpf.source requires a GTA V path for the native RPF builder"
            )
        self._require_declared_ipls(
            descriptor,
            {
                path.stem.casefold()
                for path in rpf_source.rglob("*")
                if path.is_file() and path.suffix.casefold() == ".ymap"
            },
            self._portable_source_label(source, rpf_source),
        )
        _archive, validation = RpfArchiveBuilder(
            self.project_root, self.gta_path,
        ).build(rpf_source, destination)
        return "authored_dlc_rpf", {
            "source_directory": self._portable_source_label(source, rpf_source),
        }, f"payload/{validation.name}"

    @staticmethod
    def _portable_source_label(source: Path, selected: Path | None = None) -> str:
        """Describe source evidence without embedding a creator's local path."""

        candidate = selected or source
        if source.is_dir():
            try:
                relative = candidate.relative_to(source)
            except ValueError:
                pass
            else:
                if relative.parts:
                    return relative.as_posix()
        return candidate.name

    @staticmethod
    def _indexed_ymap_names(
        scan: PackageScan, *, archive_path: str | None = None,
    ) -> set[str]:
        prefix = (
            archive_path.replace("\\", "/").casefold().rstrip("/") + "::"
            if archive_path is not None else None
        )
        return {
            PurePosixPath(entry.path.replace("\\", "/").split("::")[-1]).stem.casefold()
            for entry in scan.rpf_indexed_entries
            if entry.suffix == ".ymap"
            and (
                prefix is None
                or entry.path.replace("\\", "/").casefold().startswith(prefix)
            )
        }

    @staticmethod
    def _require_declared_ipls(
        descriptor: MapProject, available: set[str], source_label: str,
    ) -> None:
        declared = tuple(dict.fromkeys((
            *descriptor.streaming.ipls,
            *(ipl for level in descriptor.levels for ipl in level.ipls),
        )))
        missing = sorted(
            name for name in declared if name.casefold() not in available
        )
        if missing:
            raise ValueError(
                "Declared IPLs do not match indexed YMAP placements in "
                f"{source_label}: {', '.join(missing)}"
            )

    @staticmethod
    def _require_indexed_placement(
        scan: PackageScan, *, archive_path: str | None = None,
    ) -> None:
        """Prove that the RPF being copied owns a recursively indexed YMAP.

        Loose sibling files are not installed when a prebuilt dlc.rpf is
        copied. Treating those files as payload evidence could publish an
        unrelated or empty archive, so the placement must come from the
        selected RPF's native inventory.
        """

        prefix = (
            archive_path.replace("\\", "/").casefold().rstrip("/") + "::"
            if archive_path is not None else None
        )
        if any(
            entry.suffix == ".ymap"
            and (prefix is None or entry.path.replace("\\", "/").casefold().startswith(prefix))
            for entry in scan.rpf_indexed_entries
        ):
            return
        target = archive_path or str(scan.source)
        raise ValueError(
            "The selected RPF does not expose an indexed YMAP placement; "
            f"refusing to publish unverified map payload: {target}"
        )

    @staticmethod
    def _copy_member(source: Path, member: PackageEntry, destination: Path) -> None:
        if member.size > MAX_IMPORTED_MAP_RPF_BYTES:
            raise ValueError("dlc.rpf exceeds the guarded 1 GiB package import limit")
        if source.is_dir():
            candidate = (source / Path(*PurePosixPath(member.path).parts)).resolve(
                strict=True,
            )
            if not candidate.is_relative_to(source) or candidate.is_symlink():
                raise ValueError("dlc.rpf source escapes the selected package")
            shutil.copyfile(candidate, destination)
        else:
            content = PackageAssetReader(source).read(
                member.path, limit=member.size + 1,
            )
            if content.truncated or len(content.data) != member.size:
                raise ValueError("Could not read the complete dlc.rpf package member")
            destination.write_bytes(content.data)
        if destination.stat().st_size != member.size:
            raise RuntimeError("dlc.rpf copy size did not match the package inventory")

    @staticmethod
    def _find_rpf_source(source: Path) -> Path | None:
        if not source.is_dir():
            return None
        candidates: list[Path] = []
        if source.name.casefold() == "dlc.rpf.source":
            candidates.append(source)
        candidates.extend(
            path.resolve() for path in source.rglob("dlc.rpf.source")
            if path.is_dir() and not path.is_symlink()
        )
        unique = list(dict.fromkeys(candidates))
        if len(unique) > 1:
            raise ValueError(
                "Map source contains multiple dlc.rpf.source directories; select one"
            )
        return unique[0] if unique else None

    @staticmethod
    def _manifest_text(
        descriptor: MapProject,
        edition: str,
        payload_sha256: str,
        descriptor_sha256: str,
    ) -> str:
        pack_name = descriptor.streaming.pack_name
        map_destination = (
            f"scripts/ALLIN1/Maps/{descriptor.package_id}/maps.json"
        )
        return "\n".join((
            "schema_version = 2",
            f"id = {json.dumps(descriptor.package_id)}",
            f"name = {json.dumps(descriptor.name)}",
            f"version = {json.dumps(descriptor.version)}",
            'type = "mixed"',
            'description = "Map package built and validated by ALLIN1 SDK."',
            f"editions = {json.dumps([edition])}",
            'dependencies = ["openrpf"]',
            "conflicts = []",
            f"dlc_packs = [{json.dumps(pack_name)}]",
            "",
            "[allin1]",
            "api_version = 1",
            'content = "allin1.content.json"',
            'requires = ["allin1.online-content>=0.6.0"]',
            "",
            "[[files]]",
            'source = "payload/dlc.rpf"',
            f'destination = "mods/update/x64/dlcpacks/{pack_name}/dlc.rpf"',
            f"sha256 = {json.dumps(payload_sha256)}",
            "",
            "[[files]]",
            'source = "payload/maps.json"',
            f"destination = {json.dumps(map_destination)}",
            f"sha256 = {json.dumps(descriptor_sha256)}",
            "",
        ))

    @staticmethod
    def _content_manifest(descriptor: MapProject) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "api_version": 1,
            "id": descriptor.package_id,
            "name": descriptor.name,
            "version": descriptor.version,
            "description": (
                f"Managed world map '{descriptor.project_id}' with "
                f"{len(descriptor.levels)} level(s), {len(descriptor.portals)} portal(s), "
                f"and {len(descriptor.garages)} garage(s)."
            ),
            "capabilities": ["world.maps"],
            "systems": [{
                "id": f"{descriptor.package_id}.map",
                "name": descriptor.name,
                "description": "Streamed map levels, entrances, exits, and garages.",
                "category": "World Maps",
                "experimental": False,
                "enabled_by_default": True,
                "settings": [],
            }],
            "gbay": {"sections": [], "catalogs": []},
            "runtime": {"assemblies": []},
        }
