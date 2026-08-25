"""Guided vehicle import orchestration shared by desktop, CLI, and agents.

The quick path deliberately stops at the launcher's per-user package library.
It may inspect and author a validated package, but it never writes GTA V.  The
separate ALLIN1 Launcher remains the authority that presents the final install
confirmation and owns game-file receipts, backups, and rollback.
"""

from __future__ import annotations

import os
import re
import json
import shutil
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from allin1_sdk.addon_importer import AddonPackageInspector, PackageScan
from allin1_sdk.managed_package_conversion import (
    ManagedVehiclePackageConverter,
    ManagedVehiclePackagePlan,
    ManagedVehiclePackageResult,
    PublishedManagedVehiclePackage,
    storage_for_category,
)
from allin1_sdk.mods import ModManifest
from allin1_sdk.vehicle_catalog import VehicleCatalog


EDITABLE_LISTING_FIELDS = frozenset({
    "name", "manufacturer", "category", "price", "storage", "size_tier",
    "preview_dictionary", "preview_texture", "traffic_enabled",
    "traffic_weight", "free_price_confirmed",
})

# Manufacturer inference is deliberately conservative.  These names are used
# only when the package filename contains an unambiguous whole-word match; the
# SDK never guesses a brand from a model hash or an arbitrary DLC pack name.
_KNOWN_MANUFACTURERS = {
    "alfa romeo": "Alfa Romeo", "aston martin": "Aston Martin",
    "audi": "Audi", "bentley": "Bentley", "bmw": "BMW",
    "bugatti": "Bugatti", "cadillac": "Cadillac", "chevrolet": "Chevrolet",
    "dodge": "Dodge", "ferrari": "Ferrari", "ford": "Ford",
    "honda": "Honda", "hyundai": "Hyundai", "jaguar": "Jaguar",
    "koenigsegg": "Koenigsegg", "lamborghini": "Lamborghini",
    "lexus": "Lexus", "lotus": "Lotus", "maserati": "Maserati",
    "mazda": "Mazda", "mclaren": "McLaren", "mercedes": "Mercedes-Benz",
    "mercedes benz": "Mercedes-Benz", "mitsubishi": "Mitsubishi",
    "nissan": "Nissan", "pagani": "Pagani", "porsche": "Porsche",
    "renault": "Renault", "rolls royce": "Rolls-Royce",
    "subaru": "Subaru", "tesla": "Tesla", "toyota": "Toyota",
    "volkswagen": "Volkswagen", "volvo": "Volvo",
}


def friendly_vehicle_identifier(value: str) -> str:
    """Turn a technical model identifier into a readable, lossless label."""

    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value.strip())
    text = re.sub(r"[._-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.title() if text else value.strip()


def infer_vehicle_manufacturer(
    source: Path, current: str, *, model: str = "",
) -> str:
    """Keep valid metadata or use a conservative brand match from filename."""

    maker = current.strip()
    invalid = {"null", "none", "unknown", "n/a", model.strip().casefold()}
    if maker and maker.casefold() not in invalid:
        normalized_maker = re.sub(
            r"[^a-z0-9]+", " ", maker.casefold(),
        ).strip()
        canonical = {
            label for token, label in _KNOWN_MANUFACTURERS.items()
            if token == normalized_maker
        }
        if len(canonical) == 1:
            return canonical.pop()
        return maker
    words = re.sub(r"[^a-z0-9]+", " ", source.stem.casefold()).strip()
    padded = f" {words} "
    matches = {
        label for token, label in _KNOWN_MANUFACTURERS.items()
        if f" {token} " in padded
    }
    return matches.pop() if len(matches) == 1 else ""


def launcher_package_library_root(
    environment: Mapping[str, str] | None = None,
    *,
    home: str | Path | None = None,
) -> Path:
    """Return the shared, non-game package library watched by the Launcher."""

    values = os.environ if environment is None else environment
    base = str(values.get("LOCALAPPDATA", "")).strip()
    if base:
        return Path(base).expanduser().resolve() / "ALLIN1" / "Packages"
    fallback = Path(home).expanduser() if home is not None else Path.home()
    return fallback.resolve() / ".allin1" / "packages"


@dataclass(frozen=True)
class VehicleQuickImportInspection:
    """One bounded package scan and its honestly detected vehicle editions."""

    source: Path
    scan: PackageScan
    available_editions: tuple[str, ...]
    suggested_edition: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": "inspect_vehicle_quick_import",
            "source": str(self.source),
            "source_kind": self.scan.source_kind,
            "available_editions": list(self.available_editions),
            "suggested_edition": self.suggested_edition,
            "vehicles": [
                {
                    "model": item.model_name,
                    "edition": item.edition,
                    "display_name": item.game_name,
                    "manufacturer": item.make_name,
                    "vehicle_class": item.vehicle_class,
                }
                for item in self.scan.vehicles
                if item.edition.casefold() in self.available_editions
            ],
            "errors": self.scan.error_count,
            "warnings": self.scan.warning_count,
        }


@dataclass(frozen=True)
class VehicleQuickImportReview:
    """A reviewable plan plus non-blocking storefront-quality findings."""

    plan: ManagedVehiclePackagePlan
    warnings: tuple[str, ...]
    acknowledged_free_models: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": "review_vehicle_quick_import",
            "plan": self.plan.to_dict(),
            "warnings": list(self.warnings),
            "acknowledged_free_models": list(self.acknowledged_free_models),
            "ready": True,
            "game_write_authorized": False,
        }


@dataclass(frozen=True)
class PreparedVehicleQuickImport:
    """A launcher-ready package and optional deterministic distribution ZIP."""

    result: ManagedVehiclePackageResult
    published: PublishedManagedVehiclePackage | None
    warnings: tuple[str, ...]
    launcher_library: bool
    replaced_existing: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": "prepare_vehicle_quick_import",
            "game_write_performed": False,
            "launcher_install_required": True,
            "launcher_library": self.launcher_library,
            "traffic_requested": self.result.plan.traffic_opt_in,
            "replaced_existing": self.replaced_existing,
            "package": self.result.to_dict(),
            "published": self.published.to_dict() if self.published else None,
            "warnings": list(self.warnings),
        }


class VehicleQuickImportService:
    """Reduce archive-to-launcher packaging to one typed, reusable workflow."""

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
        self.converter = ManagedVehiclePackageConverter(
            self.project_root, self.gta_path, inspector=self.inspector,
        )

    def inspect(
        self, source: str | Path, *, preferred_edition: str | None = None,
    ) -> VehicleQuickImportInspection:
        resolved = Path(source).expanduser().resolve(strict=True)
        scan = self.inspector.inspect(resolved)
        if not scan.valid:
            raise ValueError(
                "Source package contains safety errors; quick import was refused"
            )
        vehicle_editions = {
            item.edition.casefold() for item in scan.vehicles
            if item.edition.casefold() in {"legacy", "enhanced"}
        }
        archive_editions = {
            item.edition.casefold() for item in scan.rpf_archives
            if item.edition.casefold() in {"legacy", "enhanced"}
        }
        available = tuple(
            edition for edition in ("legacy", "enhanced")
            if edition in vehicle_editions and edition in archive_editions
        )
        if not available:
            raise ValueError(
                "No recursively inspected Legacy or Enhanced vehicle branch was found"
            )
        preferred = (preferred_edition or "").strip().casefold()
        suggested = preferred if preferred in available else available[-1]
        return VehicleQuickImportInspection(
            source=resolved,
            scan=scan,
            available_editions=available,
            suggested_edition=suggested,
        )

    def plan(
        self,
        inspection: VehicleQuickImportInspection,
        *,
        edition: str,
        package_id: str | None = None,
        name: str | None = None,
        version: str = "1.0.0",
    ) -> VehicleQuickImportReview:
        selected = edition.strip().casefold()
        if selected not in inspection.available_editions:
            raise ValueError(
                f"Edition '{edition}' was not detected in the inspected package"
            )
        plan = self.converter.plan(
            inspection.source,
            edition=selected,
            package_id=package_id,
            name=name,
            version=version,
            scan=inspection.scan,
        )
        plan = self._refine_inferred_listings(plan, inspection)
        return VehicleQuickImportReview(plan, self.review_warnings(plan))

    def customize(
        self,
        plan: ManagedVehiclePackagePlan,
        updates: Mapping[str, Mapping[str, Any]],
    ) -> VehicleQuickImportReview:
        """Apply catalog-only listing edits without modifying the source RPF."""

        normalized_updates = {str(key).casefold(): value for key, value in updates.items()}
        known = {item.model.casefold() for item in plan.catalog.vehicles}
        unknown = sorted(set(normalized_updates) - known)
        if unknown:
            raise ValueError(
                "Listing updates reference models absent from the selected branch: "
                + ", ".join(unknown)
            )
        records: list[dict[str, Any]] = []
        acknowledged_free: set[str] = set()
        for entry in plan.catalog.vehicles:
            values = dict(entry.to_dict())
            changes = dict(normalized_updates.get(entry.model.casefold(), {}))
            unsupported = sorted(set(changes) - EDITABLE_LISTING_FIELDS)
            if unsupported:
                raise ValueError(
                    f"Unsupported listing fields for {entry.model}: "
                    + ", ".join(unsupported)
                )
            traffic = dict(values.get("traffic", {}))
            free_confirmed = changes.pop("free_price_confirmed", False)
            if not isinstance(free_confirmed, bool):
                raise ValueError(
                    f"free_price_confirmed for {entry.model} must be true or false"
                )
            if "traffic_enabled" in changes:
                traffic["enabled"] = changes.pop("traffic_enabled")
            if "traffic_weight" in changes:
                traffic["weight"] = changes.pop("traffic_weight")
            category_changed = "category" in changes
            storage_changed = "storage" in changes
            for key, value in changes.items():
                if key in {"preview_dictionary", "preview_texture"} and value in {
                    None, "",
                }:
                    values.pop(key, None)
                else:
                    values[key] = value
            if category_changed and not storage_changed:
                values["storage"] = storage_for_category(str(values["category"]))
            values["traffic"] = traffic
            if values.get("price") == 0 and free_confirmed:
                acknowledged_free.add(entry.model.casefold())
            # Package ownership is never editable through the quick path.
            values["model"] = entry.model
            values["source_pack"] = entry.source_pack
            records.append(values)

        catalog = VehicleCatalog.from_dict({
            "schema_version": 1,
            "id": plan.package_id,
            "name": plan.name,
            "vehicles": records,
        })
        catalog.validate_package_ownership((plan.dlc_pack,), allow_traffic=True)
        customized = replace(plan, catalog=catalog)
        return VehicleQuickImportReview(
            customized,
            self.review_warnings(customized, acknowledged_free),
            tuple(sorted(acknowledged_free)),
        )

    @staticmethod
    def _refine_inferred_listings(
        plan: ManagedVehiclePackagePlan,
        inspection: VehicleQuickImportInspection,
    ) -> ManagedVehiclePackagePlan:
        """Replace only clearly technical labels with safe readable defaults."""

        records: list[dict[str, Any]] = []
        for entry in plan.catalog.vehicles:
            values = entry.to_dict()
            display = entry.display_name.strip()
            if not display or display.casefold() == entry.model.casefold() or display.isupper():
                values["name"] = friendly_vehicle_identifier(entry.model)
            values["manufacturer"] = infer_vehicle_manufacturer(
                inspection.source, entry.manufacturer, model=entry.model,
            )
            records.append(values)
        catalog = VehicleCatalog.from_dict({
            "schema_version": 1,
            "id": plan.catalog.catalog_id,
            "name": plan.catalog.name,
            "vehicles": records,
        })
        return replace(plan, catalog=catalog)

    @staticmethod
    def review_warnings(
        plan: ManagedVehiclePackagePlan,
        acknowledged_free_models: set[str] | frozenset[str] = frozenset(),
    ) -> tuple[str, ...]:
        warnings: list[str] = []
        for entry in plan.catalog.vehicles:
            model = entry.model.casefold()
            display = entry.display_name.strip()
            maker = entry.manufacturer.strip()
            if display.casefold() == model or display.isupper():
                warnings.append(
                    f"{entry.model}: review the inferred display name '{display}'."
                )
            if not maker or maker.casefold() in {"null", model}:
                warnings.append(
                    f"{entry.model}: review the inferred manufacturer '{maker or '(empty)'}'."
                )
            if entry.price == 0 and model not in acknowledged_free_models:
                warnings.append(
                    f"{entry.model}: set a GBAY price or explicitly confirm a free listing."
                )
        return tuple(warnings)

    @staticmethod
    def preview_dictionary_candidates(
        inspection: VehicleQuickImportInspection,
        *,
        edition: str,
        model: str = "",
    ) -> tuple[str, ...]:
        """Return package-owned YTD dictionary names, never invented textures."""

        selected_sources = {
            item.source.casefold()
            for item in getattr(inspection.scan, "rpf_archives", ())
            if item.edition.casefold() == edition.casefold()
        }
        candidates = {
            PurePosixPath(item.path).stem
            for item in getattr(inspection.scan, "rpf_native_assets", ())
            if item.suffix.casefold() == ".ytd"
            and item.source.casefold() in selected_sources
        }
        needle = model.casefold()
        return tuple(sorted(
            candidates,
            key=lambda value: (
                0 if needle and needle in value.casefold() else 1,
                value.casefold(),
            ),
        ))

    def library_destination(
        self,
        plan: ManagedVehiclePackagePlan,
        *,
        library_root: str | Path | None = None,
    ) -> Path:
        root = Path(
            library_root if library_root is not None
            else launcher_package_library_root()
        ).expanduser().resolve()
        return root / plan.package_id

    def prepare(
        self,
        review: VehicleQuickImportReview,
        destination: str | Path,
        *,
        publish_zip: str | Path | None = None,
        library_root: str | Path | None = None,
    ) -> PreparedVehicleQuickImport:
        free_models = {
            entry.model.casefold() for entry in review.plan.catalog.vehicles
            if entry.price == 0
        }
        missing_free_ack = sorted(
            free_models - set(review.acknowledged_free_models)
        )
        if missing_free_ack:
            raise ValueError(
                "Free GBAY listings require explicit confirmation: "
                + ", ".join(missing_free_ack)
            )

        # Re-run bounded discovery immediately before writing so metadata or
        # registration changes cannot slip past an earlier UI review. The
        # customized catalog is retained, but any changed package evidence
        # requires the user to analyze and review again.
        original = review.plan
        refreshed = self.converter.plan(
            original.source,
            edition=original.edition,
            package_id=original.package_id,
            name=original.name,
            version=original.version,
            catalog=original.catalog,
        )
        evidence_fields = (
            "source_package_sha256", "source_member", "source_member_size",
            "source_member_sha256", "dlc_pack", "vehicles", "handling_ids",
            "registered_package_names", "registration_sources",
        )
        if any(
            getattr(refreshed, field) != getattr(original, field)
            for field in evidence_fields
        ):
            raise ValueError(
                "Source package changed after review; analyze it again before preparing"
            )
        output = Path(destination).expanduser().resolve(strict=False)
        replaced_existing = output.exists() or output.is_symlink()
        if replaced_existing:
            self._validate_replaceable_destination(output, refreshed.package_id)
            result = self._replace_prepared_package(refreshed, output)
        else:
            result = self.converter.export(refreshed, output)
        published = (
            self.converter.publish(result.package_root, publish_zip)
            if publish_zip is not None else None
        )
        library = Path(
            library_root if library_root is not None
            else launcher_package_library_root()
        ).expanduser().resolve()
        return PreparedVehicleQuickImport(
            result=result,
            published=published,
            warnings=review.warnings,
            launcher_library=output.parent == library,
            replaced_existing=replaced_existing,
        )

    @staticmethod
    def _validate_replaceable_destination(output: Path, package_id: str) -> None:
        """Allow replacement only for an intact SDK-authored package of this ID."""

        if output.is_symlink() or not output.is_dir():
            raise ValueError(
                "Prepared destination exists but is not a replaceable package folder"
            )
        try:
            manifest = ModManifest.load(output / "mod.toml")
            review_path = output / "allin1.review.json"
            if review_path.is_symlink() or not review_path.is_file():
                raise ValueError("managed review evidence is missing")
            evidence = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                "Prepared destination already exists and was not replaced because it "
                f"is not an intact SDK-managed package: {exc}"
            ) from exc
        if (
            manifest.mod_id != package_id
            or manifest.schema_version != 2
            or manifest.extension is None
            or not isinstance(evidence, dict)
            or evidence.get("operation") != "managed_vehicle_package_conversion"
            or evidence.get("package_id") != package_id
            or evidence.get("review_only") is not True
            or evidence.get("install_performed") is not False
            or evidence.get("source_member_sha256") != next((
                item.sha256 for item in manifest.files
                if item.destination.suffix.casefold() == ".rpf"
            ), None)
        ):
            raise ValueError(
                "Prepared destination already exists but does not prove SDK ownership "
                f"for package '{package_id}'"
            )

    def _replace_prepared_package(
        self, plan: ManagedVehiclePackagePlan, output: Path,
    ) -> ManagedVehiclePackageResult:
        """Stage a replacement, atomically swap it, and restore on swap failure."""

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}-replacement-", dir=output.parent,
        )
        os.close(descriptor)
        staged = Path(temporary_name).resolve()
        staged.unlink()
        descriptor, backup_name = tempfile.mkstemp(
            prefix=f".{output.name}-previous-", dir=output.parent,
        )
        os.close(descriptor)
        backup = Path(backup_name).resolve()
        backup.unlink()
        try:
            staged_result = self.converter.export(plan, staged)
            output.replace(backup)
            try:
                staged.replace(output)
            except Exception:
                backup.replace(output)
                raise
            # The atomic swap is the transaction boundary. Cleanup must never
            # risk restoring a partially removed backup over the verified new
            # package; a locked hidden backup is harmless and can be removed
            # on a later maintenance pass.
            shutil.rmtree(backup, ignore_errors=True)
        except Exception:
            if staged.exists() and staged.is_dir() and not staged.is_symlink():
                shutil.rmtree(staged, ignore_errors=True)
            if backup.exists() and not output.exists():
                backup.replace(output)
            raise
        return replace(
            staged_result,
            package_root=output,
            manifest_path=output / "mod.toml",
            content_path=output / "allin1.content.json",
            review_path=output / "allin1.review.json",
            payload_path=output / "payload" / "dlc.rpf",
            catalog_path=output / "payload" / "vehicles.json",
        )


def parse_listing_assignments(
    assignments: tuple[str, ...],
) -> dict[str, dict[str, Any]]:
    """Parse repeatable MODEL.FIELD=VALUE CLI assignments with strict types."""

    result: dict[str, dict[str, Any]] = {}
    for assignment in assignments:
        if "=" not in assignment or "." not in assignment.split("=", 1)[0]:
            raise ValueError("Listing edits must use MODEL.FIELD=VALUE")
        target, raw = assignment.split("=", 1)
        model, field = target.rsplit(".", 1)
        model = model.strip().casefold()
        field = field.strip()
        if not model or field not in EDITABLE_LISTING_FIELDS:
            raise ValueError(f"Unsupported listing assignment: {assignment}")
        value: Any = raw.strip()
        if field in {"price", "size_tier"}:
            try:
                value = int(value)
            except ValueError as exc:
                raise ValueError(f"{target} must be an integer") from exc
        elif field == "traffic_weight":
            try:
                value = float(value)
            except ValueError as exc:
                raise ValueError(f"{target} must be a number") from exc
        elif field in {"traffic_enabled", "free_price_confirmed"}:
            normalized = str(value).casefold()
            if normalized not in {"true", "false"}:
                raise ValueError(f"{target} must be true or false")
            value = normalized == "true"
        if field in result.setdefault(model, {}):
            raise ValueError(f"Duplicate listing assignment: {target}")
        result[model][field] = value
    return result


__all__ = [
    "EDITABLE_LISTING_FIELDS",
    "PreparedVehicleQuickImport",
    "VehicleQuickImportInspection",
    "VehicleQuickImportReview",
    "VehicleQuickImportService",
    "friendly_vehicle_identifier",
    "infer_vehicle_manufacturer",
    "launcher_package_library_root",
    "parse_listing_assignments",
]
