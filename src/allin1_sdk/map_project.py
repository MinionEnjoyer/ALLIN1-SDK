"""Evidence-backed inspection reports for GTA V map packages."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import difflib
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from allin1_sdk.addon_importer import (
    AddonPackageInspector,
    PackageEntry,
    PackageScan,
)
from allin1_sdk.rpf_tools import RpfExplorerService, RpfIndex


MAP_PROJECT_REPORT_SCHEMA_VERSION = 1
MAP_PLACEMENT_DETECTION_SCHEMA_VERSION = 2
MAX_REQUESTED_IPLS = 256
MAX_DETECTED_PLACEMENTS = 50_000
MAX_DLC_ROOT_ARCHIVES = 32
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
_MAP_SAFETY_PACKAGE_FINDINGS = frozenset({
    "rpf_map_startup_bulk_enable",
    "rpf_map_startup_eager_enable",
})
_PACK_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SEMANTIC_TOKEN_PATTERN = re.compile(r"[a-z0-9]+")
_SEMANTIC_NOISE_TOKENS = frozenset({
    "asset", "assets", "dlc", "int", "interior", "interiors", "level",
    "levels", "map", "milo", "placement", "placements", "prop", "props",
    "shell", "stream", "streaming", "v", "ymap",
})


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
class MapRootArchiveIdentity:
    """Portable identity for one root RPF inspected as part of a DLC pack."""

    source_rpf: str
    discovery_source: str
    edition: str
    archive_size: int
    placement_count: int
    inventory_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MapPlacement:
    """One discoverable YMAP placement with portable RPF provenance."""

    name: str
    source_rpf: str
    archive_path: str
    entry_path: str
    name_hash: int
    short_name_hash: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MapPlacementMatch:
    """A fail-closed resolution of one requested IPL name."""

    requested: str
    status: str
    resolved: MapPlacement | None
    candidates: tuple[MapPlacement, ...]

    @property
    def verified(self) -> bool:
        return self.status in {"exact", "semantic_unique"} and self.resolved is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": self.requested,
            "status": self.status,
            "verified": self.verified,
            "resolved": self.resolved.to_dict() if self.resolved is not None else None,
            "candidates": [item.to_dict() for item in self.candidates],
        }


@dataclass(frozen=True)
class MapPlacementDetectionReport:
    """Read-only evidence from one installed DLC's recursive RPF index."""

    pack_name: str
    edition: str
    source: str
    discovery_source: str
    archive_size: int
    inventory_fingerprint: str
    root_archives: tuple[MapRootArchiveIdentity, ...]
    placements: tuple[MapPlacement, ...]
    matches: tuple[MapPlacementMatch, ...]
    findings: tuple[MapProjectFinding, ...]

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.findings)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.findings)

    @property
    def valid(self) -> bool:
        return self.error_count == 0

    def to_dict(self) -> dict[str, Any]:
        match_counts: dict[str, int] = {}
        for match in self.matches:
            match_counts[match.status] = match_counts.get(match.status, 0) + 1
        return {
            "schema_version": MAP_PLACEMENT_DETECTION_SCHEMA_VERSION,
            "operation": "detect_map_placements",
            "pack_name": self.pack_name,
            "edition": self.edition,
            "source": self.source,
            "discovery_source": self.discovery_source,
            "archive_size": self.archive_size,
            "inventory_fingerprint": self.inventory_fingerprint,
            "summary": {
                "root_archives": len(self.root_archives),
                "placements": len(self.placements),
                "requested": len(self.matches),
                "matches": dict(sorted(match_counts.items())),
                "errors": self.error_count,
                "warnings": self.warning_count,
                "valid": self.valid,
            },
            "root_archives": [item.to_dict() for item in self.root_archives],
            "placements": [item.to_dict() for item in self.placements],
            "matches": [item.to_dict() for item in self.matches],
            "findings": [item.to_dict() for item in self.findings],
        }


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

    def detect_installed_dlc(
        self,
        pack_name: str,
        *,
        project_root: str | Path,
        gta_path: str | Path,
        expected_ipls: Iterable[str] = (),
    ) -> MapPlacementDetectionReport:
        """Recursively identify YMAP placements in one installed DLC pack.

        The operation is deliberately read-only and reports GTA-root-relative
        provenance. Enhanced packs may split map content across ``dlc.rpf``,
        ``dlc1.rpf``, and other bounded ``dlc*.rpf`` siblings. A ``mods``
        overlay wins independently for each sibling, matching the game's
        effective installed layout without exposing a developer-local path.
        """

        normalized_pack = self._pack_name(pack_name)
        game_root = Path(gta_path).expanduser().resolve(strict=True)
        if not game_root.is_dir():
            raise ValueError(f"GTA V directory not found: {game_root}")
        if (game_root / "GTA5_Enhanced.exe").is_file():
            expected_edition = "enhanced"
        elif (game_root / "GTA5.exe").is_file():
            expected_edition = "legacy"
        else:
            raise ValueError(
                "The selected GTA folder has no GTA5.exe or GTA5_Enhanced.exe."
            )
        selected = self._installed_root_archives(
            game_root, normalized_pack,
        )
        service = RpfExplorerService(project_root, game_root)
        indices = tuple(
            (service.index(archive), portable_source, discovery_source)
            for archive, portable_source, discovery_source in selected
        )
        report = self.detect_indices(
            indices,
            pack_name=normalized_pack,
            expected_ipls=expected_ipls,
        )
        if report.edition != expected_edition:
            raise RuntimeError(
                f"Indexed DLC edition '{report.edition}' does not match the "
                f"selected GTA {expected_edition} installation."
            )
        return report

    @staticmethod
    def detect_index(
        index: RpfIndex,
        *,
        pack_name: str,
        source: str,
        discovery_source: str,
        expected_ipls: Iterable[str] = (),
    ) -> MapPlacementDetectionReport:
        """Build deterministic placement evidence from an already parsed index."""

        return MapProjectResolver.detect_indices(
            ((index, source, discovery_source),),
            pack_name=pack_name,
            expected_ipls=expected_ipls,
        )

    @staticmethod
    def detect_indices(
        indices: Iterable[tuple[RpfIndex, str, str]],
        *,
        pack_name: str,
        expected_ipls: Iterable[str] = (),
    ) -> MapPlacementDetectionReport:
        """Merge placement evidence from a bounded set of split root archives."""

        normalized_pack = MapProjectResolver._pack_name(pack_name)
        requested = MapProjectResolver._requested_ipls(expected_ipls)
        selected = tuple(indices)
        if not selected:
            raise ValueError("Map placement detection requires at least one root RPF index")
        if len(selected) > MAX_DLC_ROOT_ARCHIVES:
            raise ValueError(
                f"A DLC pack may inspect at most {MAX_DLC_ROOT_ARCHIVES} root RPF "
                "archives in one detection pass."
            )
        sources = [item[1].casefold() for item in selected]
        if len(sources) != len(set(sources)):
            raise ValueError("Map placement detection received duplicate root RPF sources")
        editions = {
            MapProjectResolver._normalized_edition(index.edition)
            for index, _source, _discovery in selected
        }
        if len(editions) != 1:
            raise ValueError(
                "Split DLC root archives reported different GTA editions; detection "
                "was withheld."
            )
        edition = next(iter(editions))

        raw_placements: list[MapPlacement] = []
        root_archives: list[MapRootArchiveIdentity] = []
        findings: list[MapProjectFinding] = []
        for index, source, discovery_source in selected:
            root_placements = [
                MapPlacement(
                    name=PurePosixPath(entry.path).stem,
                    source_rpf=source,
                    archive_path=entry.archive_path,
                    entry_path=entry.path,
                    name_hash=entry.name_hash,
                    short_name_hash=entry.short_name_hash,
                )
                for entry in index.entries
                if entry.kind != "directory" and entry.suffix == ".ymap"
            ]
            raw_placements.extend(root_placements)
            root_archives.append(MapRootArchiveIdentity(
                source_rpf=source,
                discovery_source=discovery_source,
                edition=MapProjectResolver._normalized_edition(index.edition),
                archive_size=index.archive_size,
                placement_count=len(root_placements),
                inventory_fingerprint=MapProjectResolver._index_fingerprint(
                    index, source,
                ),
            ))
            findings.extend(
                MapProjectFinding(
                    "warning", "rpf_index_warning", str(message), source,
                )
                for message in index.warnings[:100]
            )
        if len(raw_placements) > MAX_DETECTED_PLACEMENTS:
            raise ValueError(
                f"Installed DLC contains {len(raw_placements):,} YMAP placements; "
                f"the guarded detection limit is {MAX_DETECTED_PLACEMENTS:,}."
            )
        placements = tuple(sorted(
            raw_placements,
            key=lambda item: (
                item.name.casefold(), item.source_rpf.casefold(),
                item.archive_path.casefold(), item.entry_path.casefold(),
            ),
        ))
        matches: list[MapPlacementMatch] = []
        by_name: dict[str, list[MapPlacement]] = {}
        for placement in placements:
            by_name.setdefault(placement.name.casefold(), []).append(placement)

        for name in requested:
            exact = tuple(by_name.get(name.casefold(), ()))
            if len(exact) == 1:
                matches.append(MapPlacementMatch(name, "exact", exact[0], exact))
                continue
            if len(exact) > 1:
                matches.append(MapPlacementMatch(name, "ambiguous", None, exact))
                findings.append(MapProjectFinding(
                    "error", "ambiguous_exact_ipl",
                    f"Requested IPL '{name}' exists in {len(exact)} indexed locations; "
                    "no location was selected.",
                    normalized_pack,
                ))
                continue

            anchors = MapProjectResolver._semantic_tokens(name)
            semantic = tuple(
                placement for placement in placements
                if len(anchors) >= 3
                and anchors.issubset(
                    MapProjectResolver._semantic_tokens(placement.name)
                )
                and difflib.SequenceMatcher(
                    None, name.casefold(), placement.name.casefold(),
                ).ratio() >= 0.72
            )
            if len(semantic) == 1:
                matches.append(MapPlacementMatch(
                    name, "semantic_unique", semantic[0], semantic,
                ))
                findings.append(MapProjectFinding(
                    "warning", "semantic_ipl_match",
                    f"Requested IPL '{name}' was not exact; the unique semantic match "
                    f"is '{semantic[0].name}'.",
                    semantic[0].entry_path,
                ))
            elif len(semantic) > 1:
                matches.append(MapPlacementMatch(
                    name, "ambiguous", None, semantic,
                ))
                findings.append(MapProjectFinding(
                    "error", "ambiguous_semantic_ipl",
                    f"Requested IPL '{name}' has {len(semantic)} semantic candidates; "
                    "no candidate was selected.",
                    normalized_pack,
                ))
            else:
                matches.append(MapPlacementMatch(name, "unresolved", None, ()))
                findings.append(MapProjectFinding(
                    "error", "unresolved_ipl",
                    f"Requested IPL '{name}' was not found in the installed DLC index.",
                    normalized_pack,
                ))

        # A runtime mapping is one-to-one: two requested names may not claim the
        # same installed placement.  Keep the evidence visible, but fail closed
        # instead of emitting a report the managed cache consumer must reject.
        resolved_groups: dict[str, list[int]] = {}
        for index, match in enumerate(matches):
            if match.verified and match.resolved is not None:
                resolved_groups.setdefault(
                    match.resolved.name.casefold(), [],
                ).append(index)
        for resolved_name, indices in resolved_groups.items():
            if len(indices) < 2:
                continue
            requested_names = [matches[index].requested for index in indices]
            for index in indices:
                match = matches[index]
                candidate = (() if match.resolved is None else (match.resolved,))
                matches[index] = MapPlacementMatch(
                    match.requested, "duplicate_resolution", None, candidate,
                )
            findings.append(MapProjectFinding(
                "error", "duplicate_resolved_ipl",
                f"Requested IPLs {', '.join(repr(item) for item in requested_names)} "
                f"all resolve to '{resolved_name}'; no mapping was selected.",
                normalized_pack,
            ))

        root_archives = sorted(
            root_archives,
            key=lambda item: (
                PurePosixPath(item.source_rpf).name.casefold(),
                item.source_rpf.casefold(),
            ),
        )
        fingerprint_payload = {
            "pack_name": normalized_pack,
            "edition": edition,
            "root_archives": [item.to_dict() for item in root_archives],
            "placements": [item.to_dict() for item in placements],
        }
        inventory_fingerprint = hashlib.sha256(json.dumps(
            fingerprint_payload, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        discovery_sources = {item.discovery_source for item in root_archives}
        return MapPlacementDetectionReport(
            pack_name=normalized_pack,
            edition=edition,
            source=root_archives[0].source_rpf,
            discovery_source=(
                next(iter(discovery_sources))
                if len(discovery_sources) == 1 else "mixed"
            ),
            archive_size=sum(item.archive_size for item in root_archives),
            inventory_fingerprint=inventory_fingerprint,
            root_archives=tuple(root_archives),
            placements=placements,
            matches=tuple(matches),
            findings=tuple(findings),
        )

    @staticmethod
    def _installed_root_archives(
        game_root: Path, pack_name: str,
    ) -> tuple[tuple[Path, str, str], ...]:
        stock_root = (
            game_root / "update" / "x64" / "dlcpacks" / pack_name
        )
        mods_root = (
            game_root / "mods" / "update" / "x64" / "dlcpacks" / pack_name
        )

        def discover(folder: Path) -> dict[str, Path]:
            if not folder.is_dir():
                return {}
            result: dict[str, Path] = {}
            for child in sorted(
                folder.iterdir(), key=lambda item: (item.name.casefold(), item.name),
            ):
                folded = child.name.casefold()
                if (
                    not child.is_file() or not folded.startswith("dlc")
                    or not folded.endswith(".rpf")
                ):
                    continue
                if folded in result:
                    raise ValueError(
                        f"DLC pack '{pack_name}' contains a case-colliding root RPF: "
                        f"{child.name}"
                    )
                result[folded] = child
            return result

        stock = discover(stock_root)
        overlay = discover(mods_root)
        names = sorted(set(stock) | set(overlay))
        if not names:
            portable = f"update/x64/dlcpacks/{pack_name}/dlc*.rpf"
            raise FileNotFoundError(
                f"Installed DLC pack '{pack_name}' has no root archives at {portable} "
                "or its mods overlay."
            )
        if len(names) > MAX_DLC_ROOT_ARCHIVES:
            raise ValueError(
                f"Installed DLC pack '{pack_name}' contains {len(names)} root RPF "
                f"archives; the guarded detection limit is {MAX_DLC_ROOT_ARCHIVES}."
            )
        selected: list[tuple[Path, str, str]] = []
        for name in names:
            authored = overlay.get(name) or stock[name]
            source_kind = "mods_overlay" if name in overlay else "game_installation"
            resolved = authored.resolve(strict=True)
            if not resolved.is_relative_to(game_root):
                raise ValueError(
                    "Installed DLC root archive resolves outside the GTA V directory"
                )
            selected.append((
                resolved, resolved.relative_to(game_root).as_posix(), source_kind,
            ))
        return tuple(selected)

    @staticmethod
    def _normalized_edition(value: str) -> str:
        edition = str(value).strip().casefold()
        if "enhanced" in edition:
            return "enhanced"
        if "legacy" in edition:
            return "legacy"
        return edition

    @staticmethod
    def _index_fingerprint(index: RpfIndex, source: str) -> str:
        payload = {
            "source_rpf": source,
            "edition": MapProjectResolver._normalized_edition(index.edition),
            "archive_size": index.archive_size,
            "archives": [
                {
                    "path": item.path,
                    "size": item.size,
                    "entry_count": item.entry_count,
                }
                for item in index.archives
            ],
            "entries": [
                {
                    "id": item.id,
                    "kind": item.kind,
                    "size": item.size,
                    "stored_size": item.stored_size,
                    "name_hash": item.name_hash,
                    "short_name_hash": item.short_name_hash,
                }
                for item in index.entries
            ],
        }
        return hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()

    @staticmethod
    def _pack_name(value: str) -> str:
        normalized = str(value).strip().casefold()
        if not _PACK_NAME_PATTERN.fullmatch(normalized):
            raise ValueError(
                "DLC pack name must use 1-64 lowercase letters, numbers, dashes, "
                "or underscores."
            )
        return normalized

    @staticmethod
    def _requested_ipls(values: Iterable[str]) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()
        for raw in values:
            name = str(raw).strip()
            if (
                not name or len(name) > 96
                or not re.fullmatch(r"[A-Za-z0-9_.:@-]+", name)
            ):
                raise ValueError(f"Invalid requested IPL name: {raw!r}")
            folded = name.casefold()
            if folded not in seen:
                seen.add(folded)
                result.append(name)
        if len(result) > MAX_REQUESTED_IPLS:
            raise ValueError(
                f"At most {MAX_REQUESTED_IPLS} IPL names may be resolved in one scan."
            )
        return tuple(result)

    @staticmethod
    def _semantic_tokens(value: str) -> frozenset[str]:
        return frozenset(
            token for token in _SEMANTIC_TOKEN_PATTERN.findall(value.casefold())
            if token not in _SEMANTIC_NOISE_TOKENS
            and not token.isdigit() and len(token) > 1
        )

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
    def inspect_scan(
        scan: PackageScan, *, require_ymap: bool = True,
    ) -> MapProjectReport:
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
            if (
                finding.severity == "error"
                or finding.code in _MAP_SAFETY_PACKAGE_FINDINGS
            ):
                findings.append(MapProjectFinding(
                    severity=finding.severity,
                    code=f"package_{finding.code}",
                    message=finding.message,
                    path=finding.path,
                ))
        roles = {item.role for item in assets}
        if require_ymap and "placement" not in roles:
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
