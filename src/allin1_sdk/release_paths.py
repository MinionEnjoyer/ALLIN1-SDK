"""Shared fail-closed filesystem boundary for release payloads and receipts."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path, PurePosixPath


def filesystem_path(path: Path) -> Path:
    absolute = os.path.abspath(path)
    if os.name != "nt" or absolute.startswith("\\\\?\\"):
        return Path(absolute)
    if absolute.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + absolute[2:])
    return Path("\\\\?\\" + absolute)


def relative_path(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"Unsafe normalized relative path: {value!r}")
    parts = value.split("/")
    for part in parts:
        if (part in {"", ".", ".."} or part != part.strip() or part.endswith(".")
                or any(ord(c) < 32 or c in '<>:"|?*' for c in part)
                or re.fullmatch(r"(?i)(con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])", part.split(".")[0])):
            raise ValueError(f"Unsafe normalized relative path: {value!r}")
    return PurePosixPath(value)


def no_links(path: Path) -> Path:
    # Do not resolve first: doing so erases evidence that a root is a junction.
    lexical = Path(os.path.abspath(path))
    for item in (*reversed(lexical.parents), lexical):
        try:
            info = filesystem_path(item).lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(f"Symlink/junction/reparse path is forbidden: {item}")
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise ValueError(f"Hard-linked release payload is forbidden: {item}")
    return lexical


def contained(root: Path, value: object) -> Path:
    base = no_links(root)
    target = no_links(base.joinpath(*relative_path(value).parts))
    if not target.resolve().is_relative_to(base.resolve()):
        raise ValueError(f"Path escapes release root: {value}")
    return target


def unique_paths(names: list[str]) -> None:
    seen: set[str] = set()
    for name in names:
        folded = relative_path(name).as_posix().casefold()
        if folded in seen:
            raise ValueError(f"Duplicate path/destination: {name}")
        seen.add(folded)
    for name in seen:
        if any(parent.as_posix() in seen for parent in PurePosixPath(name).parents if parent.as_posix() != "."):
            raise ValueError(f"File/directory destination collision: {name}")


def tree_files(root: Path) -> dict[str, Path]:
    base = no_links(root)
    if not filesystem_path(base).is_dir():
        raise ValueError(f"Release root is not a directory: {base}")
    files: dict[str, Path] = {}
    def fail(error):
        raise error
    disk_base = filesystem_path(base)
    for directory, directories, filenames in os.walk(disk_base, followlinks=False, onerror=fail):
        for name in directories + filenames:
            path = no_links(base / Path(directory).relative_to(disk_base) / name)
            relative_path(path.relative_to(base).as_posix())
            if name in filenames:
                if not filesystem_path(path).is_file():
                    raise ValueError(f"Non-regular release payload: {path}")
                files[path.relative_to(base).as_posix()] = filesystem_path(path)
    unique_paths(list(files))
    return files


def strict_json(content: str | bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(content, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"Invalid JSON number: {value}")))
