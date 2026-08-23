"""Guarded publication of vehicle DLC sources as installable ALLIN1 packages."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from allin1_sdk.addon_importer import (
    AddonPackageInspector,
    PackageAssetReader,
    PackageEntry,
    PackageScan,
)
from allin1_sdk.mods import ModManifest
from allin1_sdk.rpf_builder import RpfArchiveBuilder
from allin1_sdk.vehicle_project import VehicleProjectResolver


MAX_IMPORTED_DLC_RPF_BYTES = 1024 * 1024 * 1024
_PACK_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MOD_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _slug(value: str, *, fallback: str) -> str:
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.casefold()).strip("-_")
    normalized = normalized[:64]
    return normalized or fallback


@dataclass(frozen=True)
class VehiclePackageResult:
    root: Path
    manifest: Path
    payload: Path
    report: Path
    pack_name: str
    mod_id: str
    payload_sha256: str
    source_mode: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "root": str(self.root),
            "manifest": str(self.manifest),
            "payload": str(self.payload),
            "report": str(self.report),
            "pack_name": self.pack_name,
            "mod_id": self.mod_id,
            "payload_sha256": self.payload_sha256,
            "source_mode": self.source_mode,
        }


class VehicleAddonPackageBuilder:
    """Turn one reviewed vehicle DLC payload into a validated managed package."""

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
        destination: str | Path,
        *,
        pack_name: str | None = None,
        mod_id: str | None = None,
        name: str | None = None,
        version: str = "1.0.0",
        editions: tuple[str, ...] = ("legacy", "enhanced"),
    ) -> VehiclePackageResult:
        source_path = Path(source).expanduser().resolve()
        authoring_manifest = source_path / "vehicle-authoring.json"
        if authoring_manifest.is_file() and not authoring_manifest.is_symlink():
            from allin1_sdk.vehicle_authoring import VehicleAuthoringWorkspace

            source_path = VehicleAuthoringWorkspace(source_path).publish_source()
        scan = AddonPackageInspector().inspect(source_path)
        inferred_pack = self._infer_pack_name(scan)
        selected_pack = (pack_name or inferred_pack).casefold()
        if not _PACK_PATTERN.fullmatch(selected_pack):
            raise ValueError(
                "Vehicle DLC pack name must use 1-64 lowercase letters, numbers, "
                "dashes, or underscores"
            )
        selected_mod_id = (mod_id or f"vehicle.{selected_pack}").casefold()
        if not _MOD_ID_PATTERN.fullmatch(selected_mod_id):
            raise ValueError(
                "Vehicle package id must use 2-64 lowercase letters, numbers, dots, "
                "dashes, or underscores"
            )
        selected_editions = tuple(dict.fromkeys(item.casefold() for item in editions))
        if not selected_editions or not set(selected_editions) <= {"legacy", "enhanced"}:
            raise ValueError("Vehicle package editions may contain only Legacy and Enhanced")
        selected_version = version.strip()
        if not selected_version or any(character in selected_version for character in "\r\n"):
            raise ValueError("Vehicle package version must be a single non-empty line")
        selected_name = (name or f"{selected_pack} vehicle add-on").strip()
        if not selected_name or any(character in selected_name for character in "\r\n"):
            raise ValueError("Vehicle package name must be a single non-empty line")

        target = Path(destination).expanduser().resolve()
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Vehicle package destination already exists: {target}")
        if target == source_path or target.is_relative_to(source_path):
            raise ValueError("Vehicle package output must be outside its source tree")
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(
            prefix=f".{target.name}.vehicle-package-", dir=target.parent,
        )).resolve()
        try:
            payload = stage / "payload" / "dlc.rpf"
            payload.parent.mkdir(parents=True)
            source_mode, source_evidence, validation = self._materialize_rpf(
                source_path, scan, payload,
            )
            payload_digest = _sha256(payload)
            manifest = stage / "mod.toml"
            manifest.write_text(
                self._manifest_text(
                    selected_pack, selected_mod_id, selected_name, selected_version,
                    selected_editions, payload_digest,
                ),
                encoding="utf-8",
            )
            project = VehicleProjectResolver.inspect_scan(scan)
            report_payload = {
                "schema_version": 1,
                "operation": "vehicle_addon_package_build",
                "status": "validated",
                "source": str(source_path),
                "source_mode": source_mode,
                "source_evidence": source_evidence,
                "pack_name": selected_pack,
                "mod_id": selected_mod_id,
                "editions": list(selected_editions),
                "payload": {
                    "path": "payload/dlc.rpf",
                    "size": payload.stat().st_size,
                    "sha256": payload_digest,
                    "builder_validation": validation,
                },
                "vehicle_project": project.to_dict(),
                "safety": {
                    "source_unchanged": True,
                    "output_was_new": True,
                    "stock_game_files_modified": False,
                    "manifest_payload_validated": True,
                },
            }
            report = stage / "vehicle-package-report.json"
            report.write_text(
                json.dumps(report_payload, indent=2) + "\n", encoding="utf-8",
            )
            ModManifest.load(manifest)
            stage.rename(target)
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise
        return VehiclePackageResult(
            root=target,
            manifest=target / "mod.toml",
            payload=target / "payload" / "dlc.rpf",
            report=target / "vehicle-package-report.json",
            pack_name=selected_pack,
            mod_id=selected_mod_id,
            payload_sha256=payload_digest,
            source_mode=source_mode,
        )

    def _materialize_rpf(
        self, source: Path, scan: PackageScan, destination: Path,
    ) -> tuple[str, dict[str, Any], str | None]:
        members = tuple(
            entry for entry in scan.entries
            if PurePosixPath(entry.path).name.casefold() == "dlc.rpf"
        )
        if len(members) > 1:
            raise ValueError(
                "Vehicle package contains multiple dlc.rpf payloads; select one DLC pack"
            )
        if members:
            member = members[0]
            self._copy_member(source, member, destination)
            return "prebuilt_dlc_rpf", {
                "member": member.path, "size": member.size,
            }, None

        rpf_source = self._find_rpf_source(source)
        if rpf_source is None:
            raise ValueError(
                "No dlc.rpf payload or dlc.rpf.source authoring directory was found"
            )
        if self.gta_path is None:
            raise ValueError(
                "Building dlc.rpf.source requires a GTA V path for the native RPF builder"
            )
        archive, validation = RpfArchiveBuilder(
            self.project_root, self.gta_path,
        ).build(rpf_source, destination)
        return "authored_dlc_rpf", {
            "source_directory": str(rpf_source),
        }, f"payload/{validation.name}"

    @staticmethod
    def _copy_member(source: Path, member: PackageEntry, destination: Path) -> None:
        if member.size > MAX_IMPORTED_DLC_RPF_BYTES:
            raise ValueError("dlc.rpf exceeds the guarded 1 GiB package import limit")
        if source.is_dir():
            candidate = (
                source / Path(*PurePosixPath(member.path).parts)
            ).resolve(strict=True)
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
        candidates = []
        if source.name.casefold() == "dlc.rpf.source":
            candidates.append(source)
        candidates.extend(
            path.resolve() for path in source.rglob("dlc.rpf.source")
            if path.is_dir() and not path.is_symlink()
        )
        candidates = list(dict.fromkeys(candidates))
        if len(candidates) > 1:
            raise ValueError(
                "Vehicle source contains multiple dlc.rpf.source directories; select one"
            )
        return candidates[0] if candidates else None

    @staticmethod
    def _infer_pack_name(scan: PackageScan) -> str:
        rpf_members = [
            PurePosixPath(item.path) for item in scan.entries
            if PurePosixPath(item.path).name.casefold() == "dlc.rpf"
        ]
        for member in rpf_members:
            if len(member.parts) >= 2:
                candidate = _slug(member.parts[-2], fallback="vehiclepack")
                if candidate not in {"payload", "dlcpacks", "mods", "update", "x64"}:
                    return candidate
        for registration in scan.registrations:
            for value in registration.package_names:
                candidate = value.casefold().removeprefix("dlc_")
                candidate = _slug(candidate, fallback="vehiclepack")
                if _PACK_PATTERN.fullmatch(candidate):
                    return candidate
        if scan.vehicles:
            return _slug(scan.vehicles[0].model_name, fallback="vehiclepack")
        return _slug(scan.source.stem, fallback="vehiclepack")

    @staticmethod
    def _manifest_text(
        pack_name: str,
        mod_id: str,
        name: str,
        version: str,
        editions: tuple[str, ...],
        payload_sha256: str,
    ) -> str:
        return "\n".join((
            "schema_version = 1",
            f"id = {json.dumps(mod_id)}",
            f"name = {json.dumps(name)}",
            f"version = {json.dumps(version)}",
            'type = "rpf"',
            'description = "Vehicle DLC package built and validated by ALLIN1 SDK."',
            f"editions = {json.dumps(list(editions))}",
            'dependencies = ["openrpf"]',
            f"dlc_packs = [{json.dumps(pack_name)}]",
            "",
            "[[files]]",
            'source = "payload/dlc.rpf"',
            f'destination = "mods/update/x64/dlcpacks/{pack_name}/dlc.rpf"',
            f"sha256 = {json.dumps(payload_sha256)}",
            "",
        ))
