"""Vehicle-centered package projects for native asset inspection workflows."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from allin1_sdk.addon_importer import (
    AddonPackageInspector,
    PackageEntry,
    PackageScan,
)
from allin1_sdk.rage_data_compiler import (
    CompiledVehicle,
    RageVehicleDataCompiler,
    VehicleDataFinding,
)


PROJECT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class VehicleAssetBinding:
    """One package member linked to a specific vehicle-system role."""

    role: str
    path: str
    size: int
    required: bool
    previewable: bool


@dataclass(frozen=True)
class VehicleProjectModel:
    """Resolved native assets and metadata for one vehicle model."""

    model: str
    display_name: str
    make_name: str
    vehicle_class: str
    vehicle_type: str
    handling_id: str
    layout: str
    audio_name_hash: str
    texture_dictionary: str
    tuning_kits: tuple[str, ...]
    assets: tuple[VehicleAssetBinding, ...]
    findings: tuple[VehicleDataFinding, ...]

    @property
    def primary_model(self) -> str | None:
        return next(
            (item.path for item in self.assets if item.role == "primary_model"), None,
        )

    @property
    def high_detail_model(self) -> str | None:
        return next(
            (item.path for item in self.assets if item.role == "high_detail_model"),
            None,
        )

    @property
    def texture_asset(self) -> str | None:
        return next(
            (item.path for item in self.assets if item.role == "texture_dictionary"),
            None,
        )

    @property
    def ready_for_preview(self) -> bool:
        return self.primary_model is not None or self.high_detail_model is not None

    @property
    def complete(self) -> bool:
        return not any(item.severity == "error" for item in self.findings)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update({
            "primary_model": self.primary_model,
            "high_detail_model": self.high_detail_model,
            "texture_asset": self.texture_asset,
            "ready_for_preview": self.ready_for_preview,
            "complete": self.complete,
        })
        return payload


@dataclass(frozen=True)
class VehicleProject:
    """Portable, evidence-backed view of all vehicles in one package."""

    source: Path
    source_kind: str
    edition: str
    inventory_fingerprint: str
    models: tuple[VehicleProjectModel, ...]
    findings: tuple[VehicleDataFinding, ...]

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.findings)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.findings)

    def model(self, name: str) -> VehicleProjectModel:
        matches = [
            item for item in self.models if item.model.casefold() == name.casefold()
        ]
        if len(matches) != 1:
            raise ValueError(f"Vehicle model was not found uniquely: {name}")
        return matches[0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "source": str(self.source),
            "source_kind": self.source_kind,
            "edition": self.edition,
            "inventory_fingerprint": self.inventory_fingerprint,
            "summary": {
                "models": len(self.models),
                "previewable": sum(item.ready_for_preview for item in self.models),
                "complete": sum(item.complete for item in self.models),
                "errors": self.error_count,
                "warnings": self.warning_count,
            },
            "models": [item.to_dict() for item in self.models],
            "findings": [asdict(item) for item in self.findings],
        }

    def to_markdown(self) -> str:
        lines = [
            "# Vehicle asset project",
            "",
            f"Source: `{self.source}`",
            f"Edition: **{self.edition}**",
            f"Package inventory fingerprint: `{self.inventory_fingerprint}`",
            "",
            f"Vehicles: **{len(self.models)}** · Previewable: "
            f"**{sum(item.ready_for_preview for item in self.models)}** · "
            f"Errors: **{self.error_count}** · Warnings: **{self.warning_count}**",
            "",
            "| Vehicle | Model | High detail | Textures | Metadata | Status |",
            "|---|---|---|---|---:|---|",
        ]
        for model in self.models:
            metadata_count = sum(
                item.role.endswith("_metadata") or item.role == "registration"
                for item in model.assets
            )
            lines.append(
                f"| `{model.model}` | "
                f"{'yes' if model.primary_model else 'missing'} | "
                f"{'yes' if model.high_detail_model else 'optional'} | "
                f"{'yes' if model.texture_asset else 'missing'} | "
                f"{metadata_count} | "
                f"{'ready' if model.complete else 'needs attention'} |"
            )
        lines.extend(["", "## Findings", ""])
        if not self.findings:
            lines.append("All visible vehicle links resolved.")
        else:
            for finding in self.findings:
                model = f" `{finding.model}`" if finding.model else ""
                lines.append(
                    f"- **{finding.severity.upper()} `{finding.code}`**{model}: "
                    f"{finding.message}"
                )
        lines.append("")
        return "\n".join(lines)

    def write(self, destination: str | Path) -> Path:
        """Publish a new portable project directory without partial output."""
        target = Path(destination).expanduser().resolve()
        if target.exists() or target.is_symlink():
            raise ValueError(f"Vehicle project destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(
            prefix=f".{target.name}.allin1-stage-", dir=target.parent,
        ))
        try:
            (staging / "vehicle-project.json").write_text(
                json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8",
            )
            (staging / "vehicle-project.md").write_text(
                self.to_markdown(), encoding="utf-8",
            )
            staging.rename(target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return target / "vehicle-project.json"


class VehicleProjectResolver:
    """Resolve vehicle models, textures, metadata, and registrations as one project."""

    def inspect(
        self, source: str | Path, *, edition: str | None = None,
    ) -> VehicleProject:
        scan = AddonPackageInspector().inspect(source)
        return self.inspect_scan(scan, edition=edition)

    @staticmethod
    def inspect_scan(
        scan: PackageScan, *, edition: str | None = None,
    ) -> VehicleProject:
        compiled = RageVehicleDataCompiler.compile_scan(scan)
        entries = {item.path.casefold(): item for item in scan.entries}
        project_models = tuple(
            VehicleProjectResolver._project_model(
                vehicle, compiled.findings, entries, scan,
            )
            for vehicle in compiled.vehicles
        )
        added_findings = tuple(
            finding for model in project_models for finding in model.findings
            if finding not in compiled.findings
        )
        resolved_edition = edition or scan.edition_tag
        return VehicleProject(
            source=scan.source,
            source_kind=scan.source_kind,
            edition=resolved_edition,
            inventory_fingerprint=VehicleProjectResolver._fingerprint(scan),
            models=project_models,
            findings=compiled.findings + added_findings,
        )

    @staticmethod
    def _project_model(
        vehicle: CompiledVehicle,
        all_findings: tuple[VehicleDataFinding, ...],
        entries: dict[str, PackageEntry],
        scan: PackageScan,
    ) -> VehicleProjectModel:
        bindings: list[VehicleAssetBinding] = []

        def bind(role: str, path: str, *, required: bool = True) -> None:
            entry = entries.get(path.casefold())
            if entry is None:
                return
            bindings.append(VehicleAssetBinding(
                role=role,
                path=entry.path,
                size=entry.size,
                required=required,
                previewable=entry.suffix in {".yft", ".ytd", ".ydr", ".ydd"},
            ))

        model_key = vehicle.model.casefold()
        for path in vehicle.model_assets:
            stem = PurePosixPath(path).stem.casefold()
            suffix = PurePosixPath(path).suffix.casefold()
            if suffix == ".yft" and stem == model_key:
                role = "primary_model"
            elif suffix == ".yft" and stem == f"{model_key}_hi":
                role = "high_detail_model"
            else:
                role = "model_dependency"
            bind(role, path, required=role == "primary_model")
        for path in vehicle.texture_assets:
            bind("texture_dictionary", path)
        for path in vehicle.metadata_sources:
            name = PurePosixPath(path).name.casefold()
            if "handling" in name:
                role = "handling_metadata"
            elif "variation" in name:
                role = "variation_metadata"
            elif "carcols" in name:
                role = "tuning_metadata"
            else:
                role = "vehicle_metadata"
            bind(role, path)
        tuning_keys = {item.casefold() for item in vehicle.tuning_kits}
        for kit in scan.kits:
            if kit.name.casefold() in tuning_keys or kit.kit_id.casefold() in tuning_keys:
                bind("tuning_metadata", kit.source)
        for path in vehicle.registration_sources:
            bind("registration", path)
        for path in vehicle.label_assets:
            bind("text_labels", path, required=False)

        unique_bindings = tuple(dict.fromkeys(bindings))
        findings = tuple(
            item for item in all_findings
            if not item.model or item.model.casefold() == model_key
        )
        if not any(item.role == "primary_model" for item in unique_bindings):
            findings += (VehicleDataFinding(
                "error", "missing_vehicle_fragment", vehicle.model,
                "No primary YFT fragment matched the vehicle model name.",
            ),)
        return VehicleProjectModel(
            model=vehicle.model,
            display_name=vehicle.display_name,
            make_name=vehicle.make_name,
            vehicle_class=vehicle.vehicle_class,
            vehicle_type=vehicle.vehicle_type,
            handling_id=vehicle.handling_id,
            layout=vehicle.layout,
            audio_name_hash=vehicle.audio_name_hash,
            texture_dictionary=vehicle.texture_dictionary,
            tuning_kits=vehicle.tuning_kits,
            assets=unique_bindings,
            findings=findings,
        )

    @staticmethod
    def _fingerprint(scan: PackageScan) -> str:
        evidence = {
            "kind": scan.source_kind,
            "entries": sorted(
                ({"path": item.path, "size": item.size} for item in scan.entries),
                key=lambda item: str(item["path"]).casefold(),
            ),
        }
        encoded = json.dumps(
            evidence, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
