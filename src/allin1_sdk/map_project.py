"""Evidence-backed inspection reports for GTA V map packages."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from allin1_sdk.addon_importer import (
    AddonPackageInspector,
    PackageEntry,
    PackageScan,
)


MAP_PROJECT_REPORT_SCHEMA_VERSION = 1
_MAP_ASSET_ROLES = {
    ".ymap": "placement",
    ".ytyp": "archetypes",
    ".ybn": "collision",
    ".ydr": "drawable",
    ".ydd": "drawable_dictionary",
    ".ytd": "texture_dictionary",
    ".ynv": "navigation_mesh",
    ".ynd": "path_nodes",
    ".ymf": "map_manifest",
}


@dataclass(frozen=True)
class MapAsset:
    path: str
    size: int
    suffix: str
    role: str
    archive_entry: bool
    previewable: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MapProjectFinding:
    severity: str
    code: str
    message: str
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MapProjectReport:
    source: Path
    source_kind: str
    edition: str
    inventory_fingerprint: str
    assets: tuple[MapAsset, ...]
    findings: tuple[MapProjectFinding, ...]
    archive_count: int = 0

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.findings)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.findings)

    @property
    def valid(self) -> bool:
        return self.error_count == 0

    @property
    def role_counts(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for asset in self.assets:
            result[asset.role] = result.get(asset.role, 0) + 1
        return dict(sorted(result.items()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MAP_PROJECT_REPORT_SCHEMA_VERSION,
            "source": str(self.source),
            "source_kind": self.source_kind,
            "edition": self.edition,
            "inventory_fingerprint": self.inventory_fingerprint,
            "summary": {
                "assets": len(self.assets),
                "archives": self.archive_count,
                "roles": self.role_counts,
                "errors": self.error_count,
                "warnings": self.warning_count,
                "valid": self.valid,
            },
            "assets": [item.to_dict() for item in self.assets],
            "findings": [item.to_dict() for item in self.findings],
        }

    def write(self, destination: str | Path) -> Path:
        """Atomically publish a report directory; existing output is never replaced."""
        target = Path(destination).expanduser().resolve()
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"Map project report destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(
            prefix=f".{target.name}.map-report-", dir=target.parent,
        )).resolve()
        try:
            (staging / "map-project-report.json").write_text(
                json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8",
            )
            staging.rename(target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return target / "map-project-report.json"


class MapProjectResolver:
    """Resolve native world assets from a folder, archive, or direct RPF."""

    def inspect(
        self,
        source: str | Path,
        *,
        project_root: str | Path | None = None,
        gta_path: str | Path | None = None,
    ) -> MapProjectReport:
        scan = AddonPackageInspector(project_root, gta_path).inspect(source)
        return self.inspect_scan(scan)

    @staticmethod
    def inspect_scan(scan: PackageScan) -> MapProjectReport:
        entries = MapProjectResolver._map_entries(scan)
        assets = tuple(
            MapAsset(
                path=entry.path,
                size=entry.size,
                suffix=entry.suffix,
                role=_MAP_ASSET_ROLES[entry.suffix],
                archive_entry="::" in entry.path,
                previewable=entry.suffix in {".ydr", ".ydd", ".ytd", ".ybn"},
            )
            for entry in entries
        )
        findings: list[MapProjectFinding] = []
        for finding in scan.findings:
            if finding.severity == "error":
                findings.append(MapProjectFinding(
                    severity="error",
                    code=f"package_{finding.code}",
                    message=finding.message,
                    path=finding.path,
                ))
        roles = {item.role for item in assets}
        if "placement" not in roles:
            findings.append(MapProjectFinding(
                "error", "missing_ymap", "No YMAP placement asset was discovered.",
            ))
        has_custom_archetype_assets = bool(
            roles & {"drawable", "drawable_dictionary"}
        )
        if has_custom_archetype_assets and "archetypes" not in roles:
            findings.append(MapProjectFinding(
                "warning", "missing_ytyp",
                "Custom drawable assets were found without a YTYP archetype dictionary.",
            ))
        if "placement" in roles and not (roles & {"collision", "navigation_mesh"}):
            findings.append(MapProjectFinding(
                "warning", "no_owned_collision_or_navigation",
                "The package owns no YBN collision or YNV navigation mesh; verify that "
                "the map intentionally uses base-game collision and navigation.",
            ))
        return MapProjectReport(
            source=scan.source,
            source_kind=scan.source_kind,
            edition=scan.inspection_target_edition or scan.edition_tag,
            inventory_fingerprint=MapProjectResolver._fingerprint(scan, entries),
            assets=assets,
            findings=tuple(findings),
            archive_count=len(scan.rpf_archives),
        )

    @staticmethod
    def _map_entries(scan: PackageScan) -> tuple[PackageEntry, ...]:
        # Nested archives may contribute recursive entries even when the outer
        # source is a ZIP/folder, while direct RPF scans expose them through the
        # workbench inventory.  Merge both views by stable logical path.
        candidates = scan.workbench_entries + scan.rpf_indexed_entries
        by_path: dict[str, PackageEntry] = {}
        for entry in candidates:
            if entry.suffix in _MAP_ASSET_ROLES:
                by_path.setdefault(entry.path.casefold(), entry)
        return tuple(sorted(by_path.values(), key=lambda item: item.path.casefold()))

    @staticmethod
    def _fingerprint(
        scan: PackageScan, entries: tuple[PackageEntry, ...],
    ) -> str:
        payload = {
            "source_kind": scan.source_kind,
            "assets": [
                {"path": item.path, "size": item.size}
                for item in entries
            ],
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
