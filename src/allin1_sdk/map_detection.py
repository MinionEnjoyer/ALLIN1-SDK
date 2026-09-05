"""UI-independent recognition of bounded map package inventories."""
from __future__ import annotations

from pathlib import Path
from allin1_sdk.addon_importer import PackageScan


MAP_ASSET_SUFFIXES = frozenset({
    ".ymap", ".ytyp", ".ybn", ".ydr", ".ydd", ".ytd", ".yft",
    ".ynv", ".ynd", ".ymf",
})
MAP_PRIMARY_SUFFIXES = frozenset({".ymap", ".ytyp", ".ybn", ".ynv", ".ynd", ".ymf"})
MAP_DESCRIPTOR_NAMES = frozenset({
    "allin1.map.json", "map-project.json", "map_project.json", "map.json",
    "maps.json",
})


def map_asset_entries(scan: PackageScan | None) -> tuple[object, ...]:
    """Return the bounded package entries that can participate in a map project."""

    if scan is None:
        return ()
    return tuple(
        entry for entry in scan.workbench_entries
        if entry.suffix.casefold() in MAP_ASSET_SUFFIXES
    )


def looks_like_map_project(source: str | Path, scan: PackageScan | None = None) -> bool:
    """Recognize an explicit map descriptor or a package with map-native assets."""

    path = Path(source)
    if path.is_file() and path.name.casefold() in MAP_DESCRIPTOR_NAMES:
        return True
    if scan is not None and any(
        entry.suffix.casefold() in MAP_PRIMARY_SUFFIXES
        for entry in scan.workbench_entries
    ):
        return True
    if path.is_dir():
        return any((path / name).is_file() for name in MAP_DESCRIPTOR_NAMES)
    return False
