"""Persistent, provenance-checked visual workspaces for complete mod packages."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from allin1_sdk.addon_importer import AddonPackageInspector, PackageAssetReader
from allin1_sdk.paths import user_data_root
from allin1_sdk.rpf_builder import MAX_RPF_BUILD_FILE_BYTES
from allin1_sdk.rpf_graph import RpfPackageGraph


def _common_directory_prefix(paths: list[PurePosixPath]) -> tuple[str, ...]:
    if not paths:
        return ()
    common = list(paths[0].parts[:-1])
    for path in paths[1:]:
        parts = path.parts[:-1]
        matched = 0
        for left, right in zip(common, parts):
            if left.casefold() != right.casefold():
                break
            matched += 1
        common = common[:matched]
        if not common:
            break
    return tuple(common)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class PackageGraphProject:
    source: Path
    workspace: Path
    graph: Path
    package_fingerprint: str
    member_count: int
    sealed_rpf_count: int
    reused: bool


class PackageGraphWorkspace:
    """Import an archive/folder once and retain a safe package graph project."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root or (user_data_root() / "package-graphs")).resolve()

    def list_projects(self) -> tuple[dict[str, object], ...]:
        """Return valid retained package projects, newest first."""
        if not self.root.is_dir():
            return ()
        projects: list[dict[str, object]] = []
        for workspace in self.root.iterdir():
            if workspace.is_symlink() or not workspace.is_dir():
                continue
            graph = workspace / "package-graph.json"
            try:
                state = RpfPackageGraph.validate(graph, verify_sources=False)
            except (OSError, ValueError):
                continue
            origin = state["payload"].get("origin")
            if not isinstance(origin, dict) or origin.get("type") != "mod_package_import":
                continue
            projects.append({
                "workspace": workspace, "graph": graph,
                "source": Path(str(origin["path"])),
                "members": int(origin["entries"]),
                "sealed_rpfs": state["sealed_archive_count"],
                "expanded_rpfs": (
                    int(origin["sealed_rpfs"]) - state["sealed_archive_count"]
                ),
                "nodes": len(state["nodes"]),
                "updated": graph.stat().st_mtime,
            })
        return tuple(sorted(
            projects, key=lambda item: float(item["updated"]), reverse=True,
        ))

    def import_package(self, source: str | Path) -> PackageGraphProject:
        package = Path(source).expanduser().resolve(strict=True)
        scan = AddonPackageInspector().inspect(package)
        errors = [item for item in scan.findings if item.severity == "error"]
        if errors:
            raise ValueError(
                "Package inspection found blocking errors: "
                + "; ".join(item.message for item in errors[:5])
            )
        if not scan.entries:
            raise ValueError("The selected package contains no graphable members")
        self.root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(
            prefix=".package-graph-import-", dir=self.root,
        )).resolve()
        source_tree = staging / "package-source"
        source_tree.mkdir()
        reader = PackageAssetReader(package)
        inventory: list[dict[str, object]] = []
        package_paths = [PurePosixPath(entry.path) for entry in scan.entries]
        common_prefix = _common_directory_prefix(package_paths)
        published: Path | None = None
        try:
            for entry, authored_path in zip(scan.entries, package_paths, strict=True):
                if entry.size > MAX_RPF_BUILD_FILE_BYTES:
                    raise ValueError(
                        f"Package member exceeds the graph limit: {entry.path}"
                    )
                trimmed_parts = authored_path.parts[len(common_prefix):]
                if not trimmed_parts:
                    raise ValueError(f"Package member has no materialized name: {entry.path}")
                relative = PurePosixPath(*trimmed_parts)
                target = source_tree.joinpath(*relative.parts).resolve()
                if not target.is_relative_to(source_tree):
                    raise ValueError(
                        f"Package member escapes the graph workspace: {entry.path}"
                    )
                content = reader.read(entry.path, limit=entry.size + 1)
                if (
                    content.truncated or content.size != entry.size
                    or len(content.data) != entry.size or content.sha256 is None
                ):
                    raise ValueError(f"Package member size mismatch: {entry.path}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content.data)
                inventory.append({
                    "path": authored_path.as_posix(), "size": entry.size,
                    "sha256": content.sha256,
                })

            fingerprint = hashlib.sha256(json.dumps(
                inventory, sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")).hexdigest()
            final = self.root / f"pkg-{fingerprint[:20]}"
            final_graph = final / "package-graph.json"
            if final.exists() or final.is_symlink():
                state = RpfPackageGraph.validate(final_graph, verify_sources=True)
                origin = state["payload"].get("origin", {})
                if origin.get("package_fingerprint") != fingerprint:
                    raise ValueError(
                        f"Package graph workspace conflicts with this import: {final}"
                    )
                shutil.rmtree(staging)
                return PackageGraphProject(
                    package, final, final_graph, fingerprint, len(scan.entries),
                    state["sealed_archive_count"], True,
                )

            sealed_rpfs = sum(entry.suffix == ".rpf" for entry in scan.entries)
            archive_sha256 = _sha256_file(package) if package.is_file() else None
            origin = {
                "type": "mod_package_import", "path": str(package),
                "source_kind": scan.source_kind,
                "package_fingerprint": fingerprint,
                "entries": len(scan.entries), "sealed_rpfs": sealed_rpfs,
                "source_sha256": archive_sha256,
                "stripped_prefix": "/".join(common_prefix),
            }
            graph = RpfPackageGraph.create_from_folder(
                source_tree, staging / "package-graph.json",
                root_name="package-preview.rpf", allow_sealed_rpfs=True,
                origin=origin,
            )
            RpfPackageGraph.relocate_sources(graph, staging, final)
            report = {
                "schema_version": 1,
                "operation": "mod_package_graph_import",
                "created_utc": datetime.now(timezone.utc).isoformat(),
                "source": str(package), "source_kind": scan.source_kind,
                "package_fingerprint": fingerprint,
                "members": len(scan.entries), "sealed_rpfs": sealed_rpfs,
                "graph": "package-graph.json",
            }
            (staging / "package-graph-import.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8",
            )
            staging.rename(final)
            published = final
            RpfPackageGraph.validate(final_graph, verify_sources=True)
            return PackageGraphProject(
                package, final, final_graph, fingerprint, len(scan.entries),
                sealed_rpfs, False,
            )
        except Exception:
            if staging.is_dir() and staging.parent == self.root:
                shutil.rmtree(staging)
            if published is not None and published.is_dir() and published.parent == self.root:
                shutil.rmtree(published)
            raise
