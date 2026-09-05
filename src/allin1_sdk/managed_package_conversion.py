"""Review-only conversion of inspected vehicle add-ons into managed packages."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from allin1_sdk.addon_importer import (
    AddonPackageInspector,
    MAX_DIRECT_RPF_BYTES,
    PackageAssetReader,
    PackageScan,
)
from allin1_sdk.mods import ModManifest, open_mod_package
from allin1_sdk.paths import gta_root_containing
from allin1_sdk.vehicle_catalog import (
    ROAD_TRAFFIC_CATEGORIES,
    VehicleCatalog,
    VehicleCatalogEntry,
    VehicleTrafficPolicy,
)


MAX_CONVERTED_RPF_BYTES = 512 * 1024 * 1024


def _safe_publication_path(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("Publication paths must not use symbolic links or reparse points")

SUPPORTED_CONVERSION_EDITIONS = frozenset({"legacy", "enhanced"})
_MOD_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_PACK_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

_VEHICLE_CLASS_CATEGORIES = {
    "VC_COMPACT": "compacts", "VC_COUPES": "coupes", "VC_COUPE": "coupes",
    "VC_SEDAN": "sedans", "VC_SEDANS": "sedans", "VC_SUV": "suvs",
    "VC_MUSCLE": "muscle", "VC_SPORT": "sports", "VC_SPORTS": "sports",
    "VC_SPORT_CLASSIC": "sportsclassics", "VC_SPORTS_CLASSIC": "sportsclassics",
    "VC_SUPER": "super", "VC_OFF_ROAD": "offroad", "VC_OFFROAD": "offroad",
    "VC_MOTORCYCLE": "motorcycles", "VC_MOTORCYCLES": "motorcycles",
    "VC_VAN": "vans", "VC_VANS": "vans", "VC_BOAT": "boats",
    "VC_BOATS": "boats", "VC_HELICOPTER": "helicopters",
    "VC_HELICOPTERS": "helicopters", "VC_PLANE": "planes",
    "VC_PLANES": "planes", "VC_MILITARY": "military",
    "VC_INDUSTRIAL": "industrial", "VC_OPEN_WHEEL": "openwheel",
    "VC_OPENWHEEL": "openwheel", "VC_EMERGENCY": "emergency",
    "VC_CYCLE": "cycles", "VC_CYCLES": "cycles", "VC_SERVICE": "service",
}


def normalized_vehicle_category(value: str, vehicle_type: str = "") -> str:
    """Translate metadata to GBAY, prioritizing physical vehicle kind."""
    type_token = vehicle_type.strip().upper().replace("-", "_").replace(" ", "_")
    physical_category = {
        "VEHICLE_TYPE_BOAT": "boats",
        "VEHICLE_TYPE_HELI": "helicopters",
        "VEHICLE_TYPE_HELICOPTER": "helicopters",
        "VEHICLE_TYPE_PLANE": "planes",
    }.get(type_token)
    if physical_category:
        return physical_category
    token = value.strip().upper().replace("-", "_").replace(" ", "_")
    return _VEHICLE_CLASS_CATEGORIES.get(token, "special")


def storage_for_category(category: str) -> str:
    return {
        "boats": "harbour", "helicopters": "helipad", "planes": "hangar",
    }.get(category, "garage")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(value: str, *, limit: int = 40) -> str:
    normalized = re.sub(r"[^a-z0-9._-]+", "-", value.casefold()).strip("-._")
    return (normalized[:limit].rstrip("-._") or "vehicle")


def _title(value: str) -> str:
    words = re.sub(r"[._-]+", " ", value).strip()
    return words.title() if words else "Imported Vehicle Add-on"


def _member_edition(path: str) -> str:
    parts = {part.casefold() for part in PurePosixPath(path).parts}
    matches = [item for item in SUPPORTED_CONVERSION_EDITIONS if item in parts]
    return matches[0] if len(matches) == 1 else ""


def _registration_name(value: str) -> str:
    return value.strip().casefold().removeprefix("dlc_")


@dataclass(frozen=True)
class ManagedVehiclePackagePlan:
    """A fully resolved conversion plan that never writes to GTA V."""

    source: Path
    source_kind: str
    source_package_sha256: str | None
    edition: str
    source_member: str
    source_member_size: int
    source_member_sha256: str
    package_id: str
    name: str
    version: str
    dlc_pack: str
    destination: str
    vehicles: tuple[str, ...]
    handling_ids: tuple[str, ...]
    registered_package_names: tuple[str, ...]
    registration_sources: tuple[str, ...]
    catalog: VehicleCatalog

    @property
    def traffic_opt_in(self) -> bool:
        return any(vehicle.traffic.enabled for vehicle in self.catalog.vehicles)

    @property
    def catalog_destination(self) -> str:
        return f"scripts/ALLIN1/Catalogs/{self.package_id}/vehicles.json"

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["catalog"] = self.catalog.to_dict()
        payload["source"] = str(self.source)
        payload["schema_version"] = 1
        payload["operation"] = "managed_vehicle_package_conversion"
        payload["review_only"] = True
        payload["install_performed"] = False
        return payload

    def review_dict(self) -> dict[str, Any]:
        """Return shareable provenance without leaking the local source path."""
        payload = self.to_dict()
        payload["source"] = self.source.name
        return payload


@dataclass(frozen=True)
class ManagedVehiclePackageResult:
    package_root: Path
    manifest_path: Path
    content_path: Path
    review_path: Path
    payload_path: Path
    catalog_path: Path
    plan: ManagedVehiclePackagePlan
    launcher_contract: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": "export_managed_vehicle_package",
            "review_only": True,
            "install_performed": False,
            "package_root": str(self.package_root),
            "manifest": str(self.manifest_path),
            "content_manifest": str(self.content_path),
            "review": str(self.review_path),
            "payload": str(self.payload_path),
            "vehicle_catalog": str(self.catalog_path),
            "plan": self.plan.to_dict(),
            "launcher_contract": self.launcher_contract,
        }


@dataclass(frozen=True)
class PublishedManagedVehiclePackage:
    source_package: Path
    archive: Path
    archive_size: int
    archive_sha256: str
    members: tuple[str, ...]
    launcher_contract: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": "publish_managed_vehicle_package",
            "review_only": True,
            "install_performed": False,
            "source_package": str(self.source_package),
            "archive": str(self.archive),
            "archive_size": self.archive_size,
            "archive_sha256": self.archive_sha256,
            "members": list(self.members),
            "launcher_contract": self.launcher_contract,
        }


class ManagedVehiclePackageConverter:
    """Convert one explicit edition branch without installing or editing GTA V."""

    def __init__(
        self,
        project_root: str | Path,
        gta_path: str | Path,
        *,
        inspector: AddonPackageInspector | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        self.gta_path = Path(gta_path).expanduser().resolve()
        self.inspector = inspector or AddonPackageInspector(
            self.project_root, self.gta_path,
        )

    def plan(
        self,
        source: str | Path,
        *,
        edition: str,
        package_id: str | None = None,
        name: str | None = None,
        version: str = "0.1.0",
        scan: PackageScan | None = None,
        catalog: VehicleCatalog | None = None,
    ) -> ManagedVehiclePackagePlan:
        resolved = Path(source).expanduser().resolve(strict=True)
        selected_edition = edition.strip().casefold()
        if selected_edition not in SUPPORTED_CONVERSION_EDITIONS:
            raise ValueError("Edition must be exactly 'legacy' or 'enhanced'")
        if scan is not None and scan.source.resolve() != resolved:
            raise ValueError("Provided package scan belongs to a different source")
        scan = scan or self.inspector.inspect(resolved)
        if not scan.valid:
            raise ValueError(
                "Source package contains safety errors; conversion was refused"
            )

        all_rpf_members = [
            item for item in scan.entries
            if PurePosixPath(item.path).name.casefold() == "dlc.rpf"
        ]
        members = [
            item for item in all_rpf_members
            if _member_edition(item.path) == selected_edition
        ]
        if not members and len(all_rpf_members) == 1:
            # Many older add-ons use install/addon/<pack>/dlc.rpf without an
            # edition folder.  Accept that layout only when recursive native
            # inspection independently resolved the sole archive to the
            # explicitly selected edition.  Multiple or unresolved archives
            # remain fail-closed.
            candidate = all_rpf_members[0]
            resolved_archives = [
                item for item in scan.rpf_archives
                if item.source.casefold() == candidate.path.casefold()
                and item.edition.casefold() == selected_edition
            ]
            if _member_edition(candidate.path) == "" and len(resolved_archives) == 1:
                members = [candidate]
        if len(members) != 1:
            raise ValueError(
                f"Expected exactly one {selected_edition.title()} dlc.rpf branch; "
                f"found {len(members)}"
            )
        member = members[0]
        member_limit = (
            MAX_DIRECT_RPF_BYTES
            if scan.source_kind == "rpf" else MAX_CONVERTED_RPF_BYTES
        )
        if not 0 < member.size <= member_limit:
            raise ValueError(
                "Selected dlc.rpf is empty or exceeds the guarded conversion limit"
            )
        inspected = [
            item for item in scan.rpf_archives
            if item.source.casefold() == member.path.casefold()
            and item.edition.casefold() == selected_edition
        ]
        if len(inspected) != 1:
            raise ValueError(
                "Selected edition branch did not complete recursive RPF inspection"
            )

        direct_rpf = scan.source_kind == "rpf"

        def belongs_to_selected_archive(record_source: str) -> bool:
            return direct_rpf or record_source.casefold().startswith(
                member.path.casefold() + "!"
            )

        branch_registrations = tuple(
            item for item in scan.registrations
            if belongs_to_selected_archive(item.source)
        )
        declared_packs = tuple(dict.fromkeys(
            normalized
            for item in branch_registrations
            for value in item.package_names
            if (normalized := _registration_name(value))
            and _PACK_PATTERN.fullmatch(normalized)
        ))
        parent = PurePosixPath(member.path).parent.name
        if direct_rpf:
            directory_hint = resolved.parent.name
            if len(declared_packs) == 1:
                dlc_pack = declared_packs[0]
            elif (
                _PACK_PATTERN.fullmatch(directory_hint)
                and directory_hint.casefold() in {
                    value.casefold() for value in declared_packs
                }
            ):
                dlc_pack = directory_hint
            else:
                raise ValueError(
                    "Direct dlc.rpf has no unambiguous registered pack name. "
                    "Open the containing DLC folder or correct its setup/content "
                    "registration before Quick Import."
                )
        else:
            if not _PACK_PATTERN.fullmatch(parent):
                raise ValueError(
                    "Selected DLC branch has an unsafe or missing pack name"
                )
            dlc_pack = parent
        selected_vehicles = tuple(dict.fromkeys(
            item.model_name for item in scan.vehicles
            if item.edition.casefold() == selected_edition
            and belongs_to_selected_archive(item.source)
        ))
        if not selected_vehicles:
            raise ValueError(
                "Selected edition branch contains no promoted vehicle definitions"
            )
        handling_ids = tuple(dict.fromkeys(
            item.handling_id for item in scan.vehicles
            if item.model_name in selected_vehicles
            and item.edition.casefold() == selected_edition
        ))
        registration_records = tuple(
            item for item in scan.registrations
            if belongs_to_selected_archive(item.source)
            and any(
                _registration_name(value) == dlc_pack.casefold()
                for value in item.package_names
            )
        )
        if not registration_records:
            raise ValueError(
                f"Selected branch does not register the DLC pack '{dlc_pack}'"
            )
        registration_sources = tuple(dict.fromkeys(
            item.source for item in registration_records
        ))
        registered_package_names = tuple(dict.fromkeys(
            value for item in registration_records for value in item.package_names
            if _registration_name(value) == dlc_pack.casefold()
        ))

        if direct_rpf:
            member_sha256 = _sha256_file(resolved)
        else:
            reader = PackageAssetReader(
                resolved, project_root=self.project_root, gta_path=self.gta_path,
            )
            content = reader.read(member.path, limit=member.size + 1)
            if (
                content.truncated or len(content.data) != member.size
                or content.sha256 is None
            ):
                raise ValueError(
                    "Selected dlc.rpf could not be read and hashed exactly"
                )
            member_sha256 = content.sha256

        default_id = f"imported.{_slug(dlc_pack)}.{selected_edition}"
        normalized_id = (package_id or default_id).strip().casefold()
        if not _MOD_ID_PATTERN.fullmatch(normalized_id):
            raise ValueError(
                "Package id must be 2-64 lowercase letters, numbers, dots, "
                "dashes, or underscores"
            )
        if normalized_id.startswith("allin1."):
            raise ValueError("The allin1.* package namespace is reserved")
        normalized_name = (name or (
            f"{_title(resolved.stem)} ({selected_edition.title()})"
        )).strip()
        normalized_version = version.strip()
        if not normalized_name or not normalized_version:
            raise ValueError("Package name and version must not be empty")

        if catalog is None:
            records = {
                item.model_name.casefold(): item for item in scan.vehicles
                if item.edition.casefold() == selected_edition
                and belongs_to_selected_archive(item.source)
            }
            entries: list[VehicleCatalogEntry] = []
            for model in selected_vehicles:
                record = records[model.casefold()]
                category = normalized_vehicle_category(
                    record.vehicle_class, record.vehicle_type,
                )
                display = (record.game_name or model).strip()
                entries.append(VehicleCatalogEntry(
                    model=model.casefold(),
                    display_name=display,
                    manufacturer=record.make_name.strip(),
                    category=category,
                    price=0,
                    storage=storage_for_category(category),
                    source_pack=dlc_pack.casefold(),
                    traffic=VehicleTrafficPolicy(),
                ))
            catalog = VehicleCatalog.from_dict({
                "schema_version": 1,
                "id": normalized_id,
                "name": normalized_name,
                "vehicles": [entry.to_dict() for entry in entries],
            })
        else:
            catalog = VehicleCatalog.from_dict(catalog.to_dict())
        if catalog.catalog_id != normalized_id:
            raise ValueError("Vehicle catalog id must match the managed package id")
        catalog.validate_package_ownership(
            (dlc_pack,), allow_traffic=True,
        )
        catalog_models = {item.model.casefold() for item in catalog.vehicles}
        unknown_catalog_models = sorted(catalog_models - {
            item.casefold() for item in selected_vehicles
        })
        if unknown_catalog_models:
            raise ValueError(
                "Vehicle catalog references models absent from the selected DLC RPF: "
                + ", ".join(unknown_catalog_models)
            )

        source_digest = _sha256_file(resolved) if resolved.is_file() else None
        return ManagedVehiclePackagePlan(
            source=resolved,
            source_kind=scan.source_kind,
            source_package_sha256=source_digest,
            edition=selected_edition,
            source_member=member.path,
            source_member_size=member.size,
            source_member_sha256=member_sha256,
            package_id=normalized_id,
            name=normalized_name,
            version=normalized_version,
            dlc_pack=dlc_pack,
            destination=(
                f"mods/update/x64/dlcpacks/{dlc_pack}/dlc.rpf"
            ),
            vehicles=selected_vehicles,
            handling_ids=handling_ids,
            registered_package_names=registered_package_names,
            registration_sources=registration_sources,
            catalog=catalog,
        )

    def export(
        self, plan: ManagedVehiclePackagePlan, destination: str | Path,
    ) -> ManagedVehiclePackageResult:
        output = Path(destination).expanduser().resolve(strict=False)
        if output.exists() or output.is_symlink():
            raise ValueError(f"Managed package destination already exists: {output}")
        output_parent = output.parent.resolve()
        output_parent.mkdir(parents=True, exist_ok=True)
        if output.resolve(strict=False).is_relative_to(self.gta_path):
            raise ValueError(
                "Review packages may not be exported inside the GTA V installation"
            )
        if plan.source.is_dir() and output.resolve(strict=False).is_relative_to(
            plan.source
        ):
            raise ValueError(
                "Review packages may not be exported inside the source package"
            )

        direct_rpf = plan.source_kind == "rpf"
        content = None
        if direct_rpf:
            if (
                plan.source.stat().st_size != plan.source_member_size
                or _sha256_file(plan.source) != plan.source_member_sha256
            ):
                raise ValueError(
                    "Source dlc.rpf changed after the conversion plan was made"
                )
        else:
            reader = PackageAssetReader(
                plan.source, project_root=self.project_root, gta_path=self.gta_path,
            )
            content = reader.read(
                plan.source_member, limit=plan.source_member_size + 1,
            )
            if (
                content.truncated
                or len(content.data) != plan.source_member_size
                or content.sha256 != plan.source_member_sha256
            ):
                raise ValueError(
                    "Source dlc.rpf changed after the conversion plan was made"
                )

        staging = Path(tempfile.mkdtemp(
            prefix=f".{output.name}-", dir=output_parent,
        )).resolve()
        try:
            payload = staging / "payload" / "dlc.rpf"
            payload.parent.mkdir(parents=True)
            if direct_rpf:
                shutil.copy2(plan.source, payload)
            else:
                assert content is not None
                payload.write_bytes(content.data)
            if _sha256_file(payload) != plan.source_member_sha256:
                raise ValueError("Staged dlc.rpf failed its exact SHA-256 check")

            catalog_source = staging / "payload" / "vehicles.json"
            catalog_source.write_text(
                json.dumps(plan.catalog.to_dict(), indent=2) + "\n",
                encoding="utf-8",
            )
            catalog_sha256 = _sha256_file(catalog_source)

            manifest_path = staging / "mod.toml"
            manifest_path.write_text(
                self._manifest_text(plan, catalog_sha256), encoding="utf-8",
            )
            content_path = staging / "allin1.content.json"
            content_path.write_text(
                json.dumps(self._content_manifest(plan), indent=2) + "\n",
                encoding="utf-8",
            )
            review_path = staging / "allin1.review.json"
            review_path.write_text(
                json.dumps(plan.review_dict(), indent=2) + "\n",
                encoding="utf-8",
            )

            validated = ModManifest.load(manifest_path)
            contract = {
                "valid": True,
                "schema_version": validated.schema_version,
                "id": validated.mod_id,
                "type": validated.mod_type,
                "editions": list(validated.editions),
                "dependencies": list(validated.dependencies),
                "dlc_packs": list(validated.dlc_packs),
                "files": len(validated.files),
                "allin1_extension": validated.extension is not None,
                "payload_sha256": plan.source_member_sha256,
                "catalog_sha256": catalog_sha256,
                "traffic_opt_in": plan.traffic_opt_in,
            }
            staging.replace(output)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise

        return ManagedVehiclePackageResult(
            package_root=output,
            manifest_path=output / "mod.toml",
            content_path=output / "allin1.content.json",
            review_path=output / "allin1.review.json",
            payload_path=output / "payload" / "dlc.rpf",
            catalog_path=output / "payload" / "vehicles.json",
            plan=plan,
            launcher_contract=contract,
        )

    @staticmethod
    def review_publication(package_root: str | Path) -> dict[str, Any]:
        """Read-only inventory shared by desktop review and the existing ZIP writer."""
        raw = Path(package_root).expanduser()
        _safe_publication_path(raw)
        source = raw.resolve(strict=True)
        if not source.is_dir():
            raise ValueError("Managed package publication requires a package folder")
        # Validate redirection before parsing even the manifest's member paths.
        _safe_publication_path(source / "mod.toml")
        if (source / "mod.toml").stat().st_size > 4 * 1024 * 1024:
            raise ValueError("Publication manifest exceeds size limit")
        manifest = ModManifest.load(source)
        if (
            manifest.schema_version != 2
            or manifest.mod_type != "mixed"
            or len(manifest.editions) != 1
            or len(manifest.dlc_packs) != 1
            or len(manifest.files) != 2
            or manifest.extension is None
        ):
            raise ValueError(
                "Publication requires one validated schema-2, single-edition "
                "managed vehicle package"
            )
        relative_content = manifest.extension.manifest_path.relative_to(source)
        member_paths = {
            Path("mod.toml"),
            relative_content,
            *(Path(*item.source.parts) for item in manifest.files),
        }
        review = source / "allin1.review.json"
        _safe_publication_path(review)
        if review.is_symlink() or not review.is_file():
            raise ValueError(
                "Published managed vehicle packages require allin1.review.json"
            )
        try:
            if review.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("Managed-package review exceeds size limit")
            evidence = json.loads(review.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid managed-package review evidence: {exc}") from exc
        expected_evidence = {
            "schema_version": 1,
            "operation": "managed_vehicle_package_conversion",
            "review_only": True,
            "install_performed": False,
            "package_id": manifest.mod_id,
            "edition": manifest.editions[0],
            "dlc_pack": manifest.dlc_packs[0],
            "source_member_sha256": next(
                item.sha256 for item in manifest.files
                if item.destination.suffix.casefold() == ".rpf"
            ),
        }
        if not isinstance(evidence, dict) or any(
            evidence.get(key) != value for key, value in expected_evidence.items()
        ):
            raise ValueError(
                "Review evidence does not match the validated manifest and payload"
            )
        member_paths.add(Path("allin1.review.json"))
        ordered = tuple(sorted(
            (path.as_posix() for path in member_paths), key=str.casefold,
        ))

        members = []
        for member in ordered:
            path = source / Path(*PurePosixPath(member).parts)
            _safe_publication_path(path)
            if not path.is_file() or not path.resolve().is_relative_to(source):
                raise ValueError(f"Publish member is missing or unsafe: {member}")
            size = path.stat().st_size
            limit = MAX_CONVERTED_RPF_BYTES if path.suffix.casefold() == ".rpf" else 4 * 1024 * 1024
            if size > limit:
                raise ValueError(f"Publish member exceeds size limit: {member}")
            members.append({"path": member, "size": size, "sha256": _sha256_file(path)})
        manifest.validate_payload()
        catalog_file = next(item for item in manifest.files if item.destination.suffix.casefold() == ".json")
        catalog = VehicleCatalog.load(source / Path(*catalog_file.source.parts))
        if catalog.catalog_id != manifest.mod_id:
            raise ValueError("Vehicle catalog identity does not match its package")
        catalog.validate_package_ownership(manifest.dlc_packs, allow_traffic=True)
        return {"source_package": str(source), "package_id": manifest.mod_id,
                "name": manifest.name, "version": manifest.version, "edition": manifest.editions[0],
                "dlc_pack": manifest.dlc_packs[0], "members": members,
                "total_bytes": sum(row["size"] for row in members),
                "vehicles": [row.to_dict() for row in catalog.vehicles],
                "traffic_opt_in": any(row.traffic.enabled for row in catalog.vehicles)}


    def publish(
        self, package_root: str | Path, destination: str | Path,
        *, expected_review: dict[str, Any] | None = None,
    ) -> PublishedManagedVehiclePackage:
        """Publish one validated review folder as a deterministic ZIP archive."""
        _safe_publication_path(Path(package_root).expanduser())
        _safe_publication_path(Path(destination).expanduser())
        source = Path(package_root).expanduser().resolve(strict=True)
        if not source.is_dir():
            raise ValueError("Managed package publication requires a package folder")
        output = Path(destination).expanduser().resolve(strict=False)
        if output.suffix.casefold() != ".zip":
            raise ValueError("Published managed packages must use a .zip filename")
        if output.exists() or output.is_symlink():
            raise ValueError(f"Published package already exists: {output}")
        if output.is_relative_to(self.gta_path) or gta_root_containing(output):
            raise ValueError("Published packages may not be written inside GTA V")
        if output.is_relative_to(source):
            raise ValueError("Published archives may not be written inside their source")
        publication = self.review_publication(source)
        if expected_review is not None and publication != expected_review:
            raise ValueError("Prepared package changed after review; review publication again")
        ordered = tuple(row["path"] for row in publication["members"])
        expected_members = {row["path"]: row for row in publication["members"]}
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.stem}-", suffix=".zip", dir=output.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name).resolve()
        try:
            with zipfile.ZipFile(
                temporary, "w", compression=zipfile.ZIP_STORED,
                allowZip64=True,
            ) as archive:
                for member in ordered:
                    path = source / Path(*PurePosixPath(member).parts)
                    _safe_publication_path(path)
                    if path.is_symlink() or not path.is_file():
                        raise ValueError(f"Publish member is missing or unsafe: {member}")
                    info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
                    info.create_system = 3
                    info.external_attr = 0o100644 << 16
                    info.compress_type = zipfile.ZIP_STORED
                    info.file_size = path.stat().st_size
                    with path.open("rb") as input_stream, archive.open(
                        info, "w", force_zip64=info.file_size >= 2 * 1024**3,
                    ) as output_stream:
                        written_size = 0
                        written_digest = hashlib.sha256()
                        for chunk in iter(
                            lambda: input_stream.read(1024 * 1024), b"",
                        ):
                            output_stream.write(chunk)
                            written_size += len(chunk)
                            written_digest.update(chunk)
                    if written_size != expected_members[member]["size"] or written_digest.hexdigest() != expected_members[member]["sha256"]:
                        raise ValueError(f"Publish member changed while reading: {member}")

            with open_mod_package(temporary) as packaged:
                catalog_file = next(
                    item for item in packaged.files
                    if item.destination.suffix.casefold() == ".json"
                )
                published_catalog = VehicleCatalog.load(
                    packaged.package_root / Path(*catalog_file.source.parts)
                )
                contract = {
                    "valid": True,
                    "schema_version": packaged.schema_version,
                    "id": packaged.mod_id,
                    "type": packaged.mod_type,
                    "editions": list(packaged.editions),
                    "dependencies": list(packaged.dependencies),
                    "dlc_packs": list(packaged.dlc_packs),
                    "files": len(packaged.files),
                    "allin1_extension": packaged.extension is not None,
                    "payload_sha256": next(
                        item.sha256 for item in packaged.files
                        if item.destination.suffix.casefold() == ".rpf"
                    ),
                    "catalog_sha256": catalog_file.sha256,
                    "traffic_opt_in": any(
                        item.traffic.enabled for item in published_catalog.vehicles
                    ),
                }
            archive_size = temporary.stat().st_size
            archive_sha256 = _sha256_file(temporary)
            # Reserve this exact filename so concurrent output cannot be replaced.
            try:
                claim = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError as exc:
                raise ValueError(f"Published package already exists: {output}") from exc
            os.close(claim)
            try:
                temporary.replace(output)
            except Exception:
                output.unlink(missing_ok=True)
                raise
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        return PublishedManagedVehiclePackage(
            source_package=source,
            archive=output,
            archive_size=archive_size,
            archive_sha256=archive_sha256,
            members=ordered,
            launcher_contract=contract,
        )

    @staticmethod
    def _manifest_text(
        plan: ManagedVehiclePackagePlan, catalog_sha256: str,
    ) -> str:
        return "\n".join((
            "schema_version = 2",
            f"id = {json.dumps(plan.package_id)}",
            f"name = {json.dumps(plan.name)}",
            f"version = {json.dumps(plan.version)}",
            'type = "mixed"',
            (
                'description = "Review-only vehicle add-on package prepared and '
                'validated by ALLIN1 SDK."'
            ),
            f"editions = {json.dumps([plan.edition])}",
            'dependencies = ["openrpf"]',
            "conflicts = []",
            f"dlc_packs = {json.dumps([plan.dlc_pack])}",
            "",
            "[allin1]",
            "api_version = 1",
            'content = "allin1.content.json"',
            'requires = ["allin1.online-content>=0.5.5"]',
            "",
            "[[files]]",
            'source = "payload/dlc.rpf"',
            f"destination = {json.dumps(plan.destination)}",
            f"sha256 = {json.dumps(plan.source_member_sha256)}",
            "",
            "[[files]]",
            'source = "payload/vehicles.json"',
            f"destination = {json.dumps(plan.catalog_destination)}",
            f"sha256 = {json.dumps(catalog_sha256)}",
            "",
        ))

    @staticmethod
    def _content_manifest(plan: ManagedVehiclePackagePlan) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "api_version": 1,
            "id": plan.package_id,
            "name": plan.name,
            "version": plan.version,
            "description": (
                f"Managed {plan.edition.title()} vehicle add-on for "
                f"DLC pack {plan.dlc_pack}."
            ),
            "capabilities": [
                "gbay.catalogs",
                *(["launcher.settings", "traffic.catalog"] if plan.traffic_opt_in else []),
            ],
            "systems": [{
                "id": f"{plan.package_id}.vehicles",
                "name": f"{plan.name} Vehicles",
                "description": (
                    "Vehicle definitions: " + ", ".join(plan.vehicles)
                ),
                "category": "Vehicles",
                "experimental": False,
                "enabled_by_default": True,
                "settings": ([{
                    "key": "traffic_enabled",
                    "label": "Ambient traffic",
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "Allow eligible vehicles from this package to spawn in traffic."
                    ),
                    "group": "Traffic",
                }] if plan.traffic_opt_in else []),
            }],
            "gbay": {"sections": [], "catalogs": [{
                "id": plan.package_id,
                "kind": "vehicle",
                "source": plan.catalog_destination,
            }]},
            "runtime": {"assemblies": []},
        }
