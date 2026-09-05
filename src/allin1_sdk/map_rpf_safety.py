"""Fail-closed safety checks for map DLC startup registration.

The native archive index tells us which nested RPFs actually contain world
placement assets. ``content.xml`` and ``setup2.xml`` tell us when those
archives are eagerly enabled by ``GROUP_STARTUP``. Keeping this check in a
small pure module makes the same evidence usable by package inspection, the
map CLI, and the desktop Workbench without executing or installing a package.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable
from xml.etree import ElementTree as ET


_MAP_ARCHIVE_SUFFIXES = frozenset({
    ".ymap", ".ytyp", ".ymf", ".ybn", ".ynv", ".ynd",
})


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _direct_child(element: ET.Element, name: str) -> ET.Element | None:
    return next(
        (child for child in element if _local_name(child.tag) == name), None,
    )


def _direct_text(element: ET.Element, name: str) -> str:
    child = _direct_child(element, name)
    return "" if child is None else (child.text or "").strip()


def _items(container: ET.Element | None) -> tuple[ET.Element, ...]:
    if container is None:
        return ()
    return tuple(
        child for child in container if _local_name(child.tag) == "Item"
    )


def _rpf_reference(value: str) -> str:
    """Normalize one DLC device URI to the archive path used by an RPF index."""

    normalized = value.strip().replace("\\", "/")
    if ":/" in normalized:
        normalized = normalized.split(":/", 1)[1]
    normalized = normalized.lstrip("/")
    if normalized.casefold().startswith("%platform%/"):
        normalized = "x64/" + normalized[len("%PLATFORM%/"):]
    return PurePosixPath(normalized).as_posix().casefold()


@dataclass(frozen=True)
class StartupMapFinding:
    severity: str
    code: str
    message: str
    path: str


def inspect_startup_map_registration(
    index: Any,
    metadata: Iterable[tuple[str, ET.Element]],
) -> tuple[StartupMapFinding, ...]:
    """Report startup change sets that eagerly enable nested map archives.

    A normal DLC registration is not suspicious merely because it contains a
    YMAP. The hazardous topology requires all three facts: ``GROUP_STARTUP``
    selects a content change set, that set enables nested RPFs, and the native
    archive index proves at least one enabled RPF owns map/placement data.
    Bulk startup enablement is an error because it can exhaust GTA's map and
    fragment pools before Story Mode becomes controllable. A smaller startup
    map set remains a warning for explicit author review.
    """

    documents = tuple(metadata)
    startup_sets: set[str] = set()
    setup_sources: list[str] = []
    for source, root in documents:
        if _local_name(root.tag) != "SSetupData":
            continue
        groups = _direct_child(root, "contentChangeSetGroups")
        for group in _items(groups):
            if _direct_text(group, "NameHash").casefold() != "group_startup":
                continue
            setup_sources.append(source)
            selected = _direct_child(group, "ContentChangeSets")
            startup_sets.update(
                (item.text or "").strip().casefold()
                for item in _items(selected)
                if (item.text or "").strip()
            )
    if not startup_sets:
        return ()

    archives = {
        str(archive.path).replace("\\", "/").strip("/").casefold(): archive
        for archive in index.archives
        if str(archive.path).strip("/\\")
    }
    map_counts: dict[str, dict[str, int]] = {
        path: {"map": 0, "placement": 0} for path in archives
    }
    for entry in index.entries:
        archive_path = str(entry.archive_path).replace(
            "\\", "/",
        ).strip("/").casefold()
        suffix = str(entry.suffix).casefold()
        if archive_path not in map_counts or suffix not in _MAP_ARCHIVE_SUFFIXES:
            continue
        map_counts[archive_path]["map"] += 1
        if suffix == ".ymap":
            map_counts[archive_path]["placement"] += 1

    findings: list[StartupMapFinding] = []
    for source, root in documents:
        if _local_name(root.tag) != "CDataFileMgr__ContentsOfDataFileXml":
            continue
        declared_map_archives: set[str] = set()
        data_files = _direct_child(root, "dataFiles")
        for item in _items(data_files):
            filename = _rpf_reference(_direct_text(item, "filename"))
            if not filename.endswith(".rpf"):
                continue
            contents = _direct_text(item, "contents").casefold()
            if (
                contents == "contents_dlc_map_data"
                or "placement" in PurePosixPath(filename).name.casefold()
            ):
                declared_map_archives.add(filename)

        change_sets = _direct_child(root, "contentChangeSets")
        for change_set in _items(change_sets):
            name = _direct_text(change_set, "changeSetName")
            if not name or name.casefold() not in startup_sets:
                continue
            enabled_container = _direct_child(change_set, "filesToEnable")
            enabled = {
                _rpf_reference((item.text or "").strip())
                for item in _items(enabled_container)
                if (item.text or "").strip().casefold().endswith(".rpf")
            }
            enabled_known = enabled.intersection(archives)
            enabled_map = {
                path for path in enabled_known
                if map_counts[path]["map"] > 0 or path in declared_map_archives
            }
            if not enabled_map:
                continue
            placement_archives = {
                path for path in enabled_map
                if map_counts[path]["placement"] > 0
                or path in declared_map_archives
            }
            placement_count = sum(
                map_counts[path]["placement"] for path in enabled_map
            )
            nested_count = len(archives)
            enabled_count = len(enabled_known)
            bulk = (
                enabled_count >= 4
                and (
                    len(enabled_map) >= 4
                    or (nested_count > 0 and enabled_count / nested_count >= 0.5)
                )
            )
            if bulk:
                severity = "error"
                code = "rpf_map_startup_bulk_enable"
                disposition = (
                    "This package is not safe to publish or register until the "
                    "startup set is narrowed and the remaining map archives are "
                    "loaded on demand."
                )
            else:
                severity = "warning"
                code = "rpf_map_startup_eager_enable"
                disposition = (
                    "Review whether these archives can be activated by a scoped "
                    "IPL/content group after Story Mode is ready."
                )
            ratio = (
                f"{enabled_count}/{nested_count} indexed nested RPFs"
                if nested_count else f"{enabled_count} nested RPFs"
            )
            setup_detail = (
                f" Setup registration: {setup_sources[0]}."
                if setup_sources else ""
            )
            findings.append(StartupMapFinding(
                severity=severity,
                code=code,
                message=(
                    f"GROUP_STARTUP selects change set {name}, which enables "
                    f"{ratio}; {len(enabled_map)} contain map data and "
                    f"{len(placement_archives)} contain placement data "
                    f"({placement_count} YMAP entries). Eager bulk map loading can "
                    f"exhaust GTA streaming/pool capacity during Story Mode startup. "
                    f"{disposition}{setup_detail}"
                ),
                path=source,
            ))
    return tuple(findings)


__all__ = ["StartupMapFinding", "inspect_startup_map_registration"]
