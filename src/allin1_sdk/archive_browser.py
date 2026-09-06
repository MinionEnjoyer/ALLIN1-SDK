"""Read-only filesystem/RPF navigation with exact archive member identities."""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
from itertools import islice
from typing import Any, Iterator

from allin1_sdk.paths import project_root
from allin1_sdk.release_paths import no_links, relative_path
from allin1_sdk.rpf_tools import RpfExplorerService

PAGE_SIZE = 100
MAX_FILES = 100_000
MAX_ARCHIVES = 1_000
MAX_ARCHIVE_ENTRIES = 250_000
MAX_OFFSET = 100_000
MAX_DEPTH = 128


def _text(value: object, label: str, limit: int = 4096) -> str:
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError(f"{label} must be bounded text without control characters")
    return value


def _relative(value: object, label: str) -> str:
    text = _text(value, label).replace("\\", "/")
    return relative_path(text).as_posix() if text else ""


def _root(value: object, label: str) -> Path:
    text = _text(value, label)
    if not text or not Path(text).is_absolute():
        raise ValueError(f"{label} must be an absolute directory")
    path = no_links(Path(text))
    if not path.is_dir():
        raise ValueError(f"{label} must be an existing directory")
    return path.resolve(strict=True)


def _context(payload: dict[str, Any]):
    if not isinstance(payload, dict):
        raise ValueError("Archive browser payload must be an object")
    root = _root(payload.get("root"), "Browser root")
    game = _root(payload["gta_path"], "GTA path") if payload.get("gta_path") else None
    path = _relative(payload.get("path", ""), "Location")
    target = no_links(root / path)
    if not target.resolve(strict=True).is_relative_to(root):
        raise ValueError("Location escapes the browser root")
    offset = payload.get("offset", 0)
    if type(offset) is not int or not 0 <= offset <= MAX_OFFSET:
        raise ValueError("Browser offset is outside the supported range")
    return root, game, target, path, offset


def _location(path: str = "", layer: str = "", directory: str = "") -> dict[str, str]:
    return {"path": path, "layer": layer, "directory": directory}


def _filesystem_entry(root: Path, path: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    is_dir = path.is_dir()
    kind = "directory" if is_dir else "archive" if path.suffix.casefold() == ".rpf" else "file"
    return {
        "id": relative, "name": path.name, "path": relative, "kind": kind,
        "size": 0 if is_dir else path.stat().st_size,
        "source": str(path), "archive": None, "entry_id": None,
        "origin": "mods" if relative.split("/")[0].casefold() == "mods" else "source",
        "location": _location(relative) if kind in {"directory", "archive"} else None,
    }


def _member(archive: Path, relative: str, entry) -> dict[str, Any]:
    kind = entry.kind
    if kind == "directory":
        location = _location(relative, entry.archive_path, entry.path)
    elif kind == "archive":
        layer = "/".join(filter(None, (entry.archive_path, entry.path)))
        location = _location(relative, layer)
    else:
        location = None
    return {
        "id": f"{relative}::{entry.id}", "name": entry.name,
        "path": f"{relative}::{entry.virtual_name}", "kind": kind,
        "size": entry.size, "source": str(archive), "archive": str(archive),
        "entry_id": entry.id, "archive_path": entry.archive_path,
        "member_path": entry.path, "location": location,
        "origin": "mods" if relative.split("/")[0].casefold() == "mods" else "source",
    }


def _result(root: Path, game: Path | None, location: dict, entries: list, offset: int,
            total: int, warnings: list, *, complete: bool, **extra) -> dict[str, Any]:
    return {
        "kind": "archive_browser", "root": str(root),
        "gta_path": str(game) if game else None, "location": location,
        "entries": entries, "offset": offset, "page_size": PAGE_SIZE,
        "matched_count": total, "has_more": total > offset + len(entries),
        "scan_complete": complete, "warnings": warnings[:100],
        "read_only": True, "game_write_performed": False, **extra,
    }


def browse(payload: dict[str, Any]) -> dict[str, Any]:
    root, game, target, path, offset = _context(payload)
    layer = _relative(payload.get("layer", ""), "Archive layer")
    directory = _relative(payload.get("directory", ""), "Archive directory")
    query = _text(payload.get("query", ""), "Filter", 256).casefold()
    entries, warnings = [], []
    complete, edition = True, None
    up = PurePosixPath(path).parent.as_posix()
    parent = _location("" if up == "." else up) if path else None
    if target.is_dir():
        if layer or directory:
            raise ValueError("Archive layer/directory require an RPF location")
        with os.scandir(target) as children:
            for count, child in enumerate(children):
                if count >= MAX_FILES:
                    complete = False
                    warnings.append("Folder listing reached its file limit.")
                    break
                try:
                    item = _filesystem_entry(root, no_links(Path(child.path)))
                    if not query or query in item["name"].casefold():
                        entries.append(item)
                except (OSError, ValueError) as exc:
                    complete = False
                    if len(warnings) < 100:
                        warnings.append(f"Skipped {child.name}: {exc}")
    else:
        if target.suffix.casefold() != ".rpf":
            raise ValueError("Browse location must be a folder or RPF")
        if game is None:
            raise ValueError("Choose the matching GTA installation to open RPF archives")
        index = RpfExplorerService(project_root(), game).index(target)
        edition = index.edition
        warnings.extend(index.warnings[:100])
        complete = not bool(index.warnings)
        layers = {item.path for item in index.archives}
        if layer not in layers:
            raise ValueError("Archive layer is not present in the selected RPF")
        if directory and not any(e.archive_path == layer and e.path == directory and e.kind == "directory" for e in index.entries):
            raise ValueError("Archive directory is not present in the selected layer")
        if directory:
            up = PurePosixPath(directory).parent.as_posix()
            parent = _location(path, layer, "" if up == "." else up)
        elif layer:
            owners = [e for e in index.entries if e.kind == "archive" and "/".join(filter(None, (e.archive_path, e.path))) == layer]
            if len(owners) != 1:
                raise ValueError("Nested archive has ambiguous parent identity")
            owner = owners[0]
            up = PurePosixPath(owner.path).parent.as_posix()
            parent = _location(path, owner.archive_path, "" if up == "." else up)
        for entry in index.entries:
            container = PurePosixPath(entry.path).parent.as_posix()
            if entry.archive_path == layer and ("" if container == "." else container) == directory:
                if not query or query in entry.name.casefold():
                    entries.append({**_member(target, path, entry), "edition": index.edition})
    entries.sort(key=lambda item: (item["kind"] not in {"directory", "archive"}, item["name"].casefold(), item["id"]))
    return _result(root, game, _location(path, layer, directory), entries[offset:offset + PAGE_SIZE], offset,
                   len(entries), warnings, complete=complete, parent=parent, edition=edition)


def search(payload: dict[str, Any]) -> dict[str, Any]:
    root, game, _target, _path, offset = _context({**payload, "path": ""})
    query = _text(payload.get("query", ""), "Search", 256).strip().casefold()
    if not query:
        raise ValueError("Enter a filename or path to search")
    scope = payload.get("scope", "all")
    if scope not in {"all", "source", "mods"}:
        raise ValueError("Search scope must be all, source, or mods")
    entries, warnings = [], []
    matched = files = archives = members = 0
    complete = True

    def add(item):
        nonlocal matched
        if query in item["path"].casefold():
            if offset <= matched < offset + PAGE_SIZE:
                entries.append(item)
            matched += 1

    def walk(folder: Path, depth: int = 0) -> Iterator[Path]:
        nonlocal complete, files
        if depth > MAX_DEPTH:
            complete = False
            return
        try:
            with os.scandir(folder) as children:
                # Stable ordering makes paging repeatable for an unchanged tree.
                # Bound collection before sorting, including hostile huge folders.
                batch = list(islice(children, max(0, MAX_FILES - files) + 1))
                if len(batch) > MAX_FILES - files:
                    complete = False
                for child in sorted(batch, key=lambda item: (item.name.casefold(), item.name)):
                    files += 1
                    if files > MAX_FILES:
                        complete = False
                        return
                    try:
                        candidate = no_links(Path(child.path))
                        relative = candidate.relative_to(root)
                        is_mods = relative.parts[0].casefold() == "mods"
                        if scope == "source" and is_mods:
                            continue
                        yield candidate
                        if candidate.is_dir():
                            yield from walk(candidate, depth + 1)
                    except (OSError, ValueError) as exc:
                        complete = False
                        if len(warnings) < 100:
                            warnings.append(f"Skipped {child.name}: {exc}")
        except OSError as exc:
            complete = False
            if len(warnings) < 100:
                warnings.append(str(exc))

    start = no_links(root / "mods") if scope == "mods" else root
    if not start.is_dir():
        raise ValueError("The selected root has no mods folder")
    for candidate in walk(start):
        try:
            item = _filesystem_entry(root, candidate)
        except (OSError, ValueError) as exc:
            complete = False
            if len(warnings) < 100:
                warnings.append(f"Skipped {candidate.name}: {exc}")
            continue
        add(item)
        if item["kind"] != "archive":
            continue
        if game is None:
            complete = False
            if not warnings:
                warnings.append("RPF members were not searched: choose the matching GTA installation.")
            continue
        if archives >= MAX_ARCHIVES or members >= MAX_ARCHIVE_ENTRIES:
            complete = False
            break
        archives += 1
        try:
            index = RpfExplorerService(project_root(), game).index(candidate)
            for entry in sorted(index.entries, key=lambda item: (item.id.casefold(), item.id)):
                if members >= MAX_ARCHIVE_ENTRIES:
                    complete = False
                    break
                members += 1
                add({**_member(candidate, candidate.relative_to(root).as_posix(), entry), "edition": index.edition})
            if index.warnings:
                complete = False
                warnings.extend(index.warnings[:max(0, 100-len(warnings))])
        except (OSError, ValueError, RuntimeError) as exc:
            complete = False
            if len(warnings) < 100:
                warnings.append(f"{candidate.name}: {exc}")
    if not complete and len(warnings) < 100:
        warnings.append("Search coverage is incomplete; narrow the root or scope and review skipped archives.")
    return _result(root, game, _location(), entries, offset, matched, warnings,
                   complete=complete, query=query, scope=scope, scanned_files=files,
                   scanned_archives=archives, scanned_members=members, parent=None)
