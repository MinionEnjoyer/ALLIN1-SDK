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
from allin1_sdk.vehicle_catalog import (
    VehicleCatalog,
    VehicleCatalogEntry,
    VehicleTrafficPolicy,
)
from allin1_sdk.managed_package_conversion import (
    normalized_vehicle_category,
    storage_for_category,
)


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
    catalog: Path
    content_manifest: Path
    profiles: Path | None = None

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
            "catalog": str(self.catalog),
            "content_manifest": str(self.content_manifest),
            "profiles": str(self.profiles) if self.profiles is not None else None,
        }


@dataclass(frozen=True)
class VehiclePackageReview:
    source: Path
    destination: Path
    pack_name: str
    mod_id: str
    name: str
    version: str
    editions: tuple[str, ...]
    source_mode: str
    source_evidence: dict[str, Any]
    inventory_fingerprint: str
    models: tuple[str, ...]
    catalog: dict[str, Any]
    authoring_profiles: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source": str(self.source),
            "destination": str(self.destination),
            "pack_name": self.pack_name,
            "mod_id": self.mod_id,
            "name": self.name,
            "version": self.version,
            "editions": list(self.editions),
            "source_mode": self.source_mode,
            "source_evidence": self.source_evidence,
            "inventory_fingerprint": self.inventory_fingerprint,
            "models": list(self.models),
            "catalog": self.catalog,
            "authoring_profiles": self.authoring_profiles,
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
        catalog: VehicleCatalog | None = None,
    ) -> VehiclePackageResult:
        review = self.review(
            source, destination, pack_name=pack_name, mod_id=mod_id,
            name=name, version=version, editions=editions, catalog=catalog,
        )
        source_path = review.source
        scan = AddonPackageInspector().inspect(source_path)
        selected_pack = review.pack_name
        selected_mod_id = review.mod_id
        selected_editions = review.editions
        selected_version = review.version
        selected_name = review.name
        target = review.destination
        catalog = VehicleCatalog.from_dict(review.catalog)
        authoring_profiles = review.authoring_profiles
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
            project = VehicleProjectResolver.inspect_scan(scan)
            traffic_opt_in = any(item.traffic.enabled for item in catalog.vehicles)
            catalog_path = stage / "payload" / "vehicles.json"
            catalog_path.write_text(
                json.dumps(catalog.to_dict(), indent=2) + "\n", encoding="utf-8",
            )
            catalog_digest = _sha256(catalog_path)
            profiles_path: Path | None = None
            profiles_digest: str | None = None
            if authoring_profiles:
                profiles_path = stage / "payload" / "vehicle-profiles.json"
                profiles_path.write_text(
                    json.dumps(authoring_profiles, indent=2) + "\n", encoding="utf-8",
                )
                profiles_digest = _sha256(profiles_path)
            content_path = stage / "allin1.content.json"
            content_path.write_text(
                json.dumps(self._content_manifest(
                    selected_mod_id, selected_name, selected_version,
                    selected_pack, catalog, traffic_opt_in,
                ), indent=2) + "\n",
                encoding="utf-8",
            )
            manifest = stage / "mod.toml"
            manifest.write_text(
                self._manifest_text(
                    selected_pack, selected_mod_id, selected_name, selected_version,
                    selected_editions, payload_digest, catalog_digest,
                    profiles_digest,
                ),
                encoding="utf-8",
            )
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
                "vehicle_catalog": catalog.to_dict(),
                "vehicle_profiles": authoring_profiles or None,
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
            catalog=target / "payload" / "vehicles.json",
            content_manifest=target / "allin1.content.json",
            profiles=(
                target / "payload" / "vehicle-profiles.json"
                if authoring_profiles else None
            ),
        )

    def review(
        self,
        source: str | Path,
        destination: str | Path,
        *,
        pack_name: str | None = None,
        mod_id: str | None = None,
        name: str | None = None,
        version: str = "1.0.0",
        editions: tuple[str, ...] = ("legacy", "enhanced"),
        catalog: VehicleCatalog | None = None,
    ) -> VehiclePackageReview:
        source_path = Path(source).expanduser().resolve()
        authoring_manifest = source_path / "vehicle-authoring.json"
        authoring_workspace = None
        authoring_profiles: dict[str, Any] = {}
        if authoring_manifest.is_file() and not authoring_manifest.is_symlink():
            from allin1_sdk.vehicle_authoring import VehicleAuthoringWorkspace

            authoring_workspace = VehicleAuthoringWorkspace(source_path)
            raw_axles = authoring_workspace.manifest.get("axle_configurations", {})
            raw_transmissions = authoring_workspace.manifest.get(
                "transmission_configurations", {},
            )
            if not isinstance(raw_axles, dict) or not isinstance(raw_transmissions, dict):
                raise ValueError("Vehicle authoring profiles are invalid")
            if raw_axles or raw_transmissions:
                authoring_profiles = {
                    "schema_version": 1,
                    "axle_configurations": raw_axles,
                    "transmission_configurations": raw_transmissions,
                }
            source_path = authoring_workspace.publish_source()
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
        source_mode, source_evidence = self._review_source_mode(source_path, scan)
        project = VehicleProjectResolver.inspect_scan(scan)
        if catalog is None and authoring_workspace is not None:
            catalog = authoring_workspace.distribution_catalog(
                selected_mod_id, selected_name, selected_pack,
            )
        if catalog is None:
            entries = []
            for item in project.models:
                category = normalized_vehicle_category(
                    item.vehicle_class, item.vehicle_type,
                )
                entries.append(VehicleCatalogEntry(
                    model=item.model.casefold(),
                    display_name=item.display_name or item.model,
                    manufacturer=item.make_name,
                    category=category,
                    price=0,
                    storage=storage_for_category(category),
                    source_pack=selected_pack,
                    traffic=VehicleTrafficPolicy(),
                ))
            catalog = VehicleCatalog.from_dict({
                "schema_version": 1,
                "id": selected_mod_id,
                "name": selected_name,
                "vehicles": [entry.to_dict() for entry in entries],
            })
        else:
            catalog = VehicleCatalog.from_dict(catalog.to_dict())
        if catalog.catalog_id != selected_mod_id:
            raise ValueError("Vehicle catalog id must match the package id")
        catalog.validate_package_ownership((selected_pack,), allow_traffic=True)
        owned_models = {item.model.casefold() for item in project.models}
        unknown_catalog_models = sorted(
            item.model for item in catalog.vehicles
            if item.model.casefold() not in owned_models
        )
        if unknown_catalog_models:
            raise ValueError(
                "Vehicle catalog references models absent from the selected DLC "
                "source: " + ", ".join(unknown_catalog_models)
            )
        return VehiclePackageReview(
            source=source_path,
            destination=target,
            pack_name=selected_pack,
            mod_id=selected_mod_id,
            name=selected_name,
            version=selected_version,
            editions=selected_editions,
            source_mode=source_mode,
            source_evidence=source_evidence,
            inventory_fingerprint=project.inventory_fingerprint,
            models=tuple(item.model for item in project.models),
            catalog=catalog.to_dict(),
            authoring_profiles=authoring_profiles,
        )

    def _review_source_mode(
        self, source: Path, scan: PackageScan,
    ) -> tuple[str, dict[str, Any]]:
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
            if member.size > MAX_IMPORTED_DLC_RPF_BYTES:
                raise ValueError("dlc.rpf exceeds the guarded 1 GiB package import limit")
            return "prebuilt_dlc_rpf", {"member": member.path, "size": member.size}
        rpf_source = self._find_rpf_source(source)
        if rpf_source is None:
            raise ValueError(
                "No dlc.rpf payload or dlc.rpf.source authoring directory was found"
            )
        if self.gta_path is None:
            raise ValueError(
                "Building dlc.rpf.source requires a GTA V path for the native RPF builder"
            )
        return "authored_dlc_rpf", {"source_directory": str(rpf_source)}

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
        catalog_sha256: str,
        profiles_sha256: str | None = None,
    ) -> str:
        lines = [
            "schema_version = 2",
            f"id = {json.dumps(mod_id)}",
            f"name = {json.dumps(name)}",
            f"version = {json.dumps(version)}",
            'type = "mixed"',
            'description = "Vehicle DLC package built and validated by ALLIN1 SDK."',
            f"editions = {json.dumps(list(editions))}",
            'dependencies = ["openrpf"]',
            "conflicts = []",
            f"dlc_packs = [{json.dumps(pack_name)}]",
            "",
            "[allin1]",
            "api_version = 1",
            'content = "allin1.content.json"',
            'requires = ["allin1.online-content>=0.5.5"]',
            "",
            "[[files]]",
            'source = "payload/dlc.rpf"',
            f'destination = "mods/update/x64/dlcpacks/{pack_name}/dlc.rpf"',
            f"sha256 = {json.dumps(payload_sha256)}",
            "",
            "[[files]]",
            'source = "payload/vehicles.json"',
            f'destination = "scripts/ALLIN1/Catalogs/{mod_id}/vehicles.json"',
            f"sha256 = {json.dumps(catalog_sha256)}",
            "",
        ]
        if profiles_sha256 is not None:
            lines.extend((
                "[[files]]",
                'source = "payload/vehicle-profiles.json"',
                f'destination = "scripts/ALLIN1/VehicleProfiles/{mod_id}.json"',
                f"sha256 = {json.dumps(profiles_sha256)}",
                "",
            ))
        return "\n".join(lines)

    @staticmethod
    def _content_manifest(
        mod_id: str, name: str, version: str, pack_name: str,
        catalog: VehicleCatalog, traffic_opt_in: bool,
    ) -> dict[str, Any]:
        capabilities = ["gbay.catalogs"]
        settings: list[dict[str, Any]] = []
        if traffic_opt_in:
            capabilities.extend(("launcher.settings", "traffic.catalog"))
            settings.append({
                "key": "traffic_enabled",
                "label": "Ambient traffic",
                "type": "boolean",
                "default": False,
                "description": (
                    "Allow eligible vehicles from this package to spawn in traffic."
                ),
                "group": "Traffic",
            })
        return {
            "schema_version": 1,
            "api_version": 1,
            "id": mod_id,
            "name": name,
            "version": version,
            "description": f"Managed vehicle add-on for DLC pack {pack_name}.",
            "capabilities": capabilities,
            "systems": [{
                "id": f"{mod_id}.vehicles",
                "name": f"{name} Vehicles",
                "description": "Vehicle definitions: " + ", ".join(
                    item.model for item in catalog.vehicles
                ),
                "category": "Vehicles",
                "experimental": False,
                "enabled_by_default": True,
                "settings": settings,
            }],
            "gbay": {"sections": [], "catalogs": [{
                "id": mod_id,
                "kind": "vehicle",
                "source": f"scripts/ALLIN1/Catalogs/{mod_id}/vehicles.json",
            }]},
            "runtime": {"assemblies": []},
        }
