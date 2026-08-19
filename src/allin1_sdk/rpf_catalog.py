"""Incremental cross-archive RPF catalog and global entry search."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from allin1_sdk.rpf_tools import RpfExplorerService


RPF_CATALOG_SCHEMA = 1
MAX_CATALOG_ARCHIVES = 512
MAX_SEARCH_RESULTS = 5_000
ProgressCallback = Callable[[str, int], None]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


@dataclass(frozen=True)
class RpfCatalogResult:
    outer_archive: str
    archive_path: str
    entry_path: str
    kind: str
    size: int
    suffix: str
    edition: str
    resource_version: int | None

    @property
    def virtual_name(self) -> str:
        return (
            f"{self.outer_archive}::{self.archive_path}::{self.entry_path}"
            if self.archive_path else f"{self.outer_archive}::{self.entry_path}"
        )


class RpfCatalogService:
    """Build an atomic SQLite catalog and search it without opening every RPF."""

    def __init__(self, project_root: str | Path, gta_path: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.gta_path = Path(gta_path).resolve()
        self.explorer = RpfExplorerService(self.project_root, self.gta_path)

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript("""
            PRAGMA user_version = 1;
            CREATE TABLE catalog_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE archives (
                source TEXT PRIMARY KEY,
                relative_path TEXT NOT NULL,
                file_size INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                edition TEXT NOT NULL,
                nested_archives INTEGER NOT NULL,
                entry_count INTEGER NOT NULL,
                error TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE entries (
                source TEXT NOT NULL,
                entry_id TEXT NOT NULL,
                archive_path TEXT NOT NULL,
                entry_path TEXT NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                size INTEGER NOT NULL,
                stored_size INTEGER NOT NULL,
                suffix TEXT NOT NULL,
                resource_version INTEGER,
                search_text TEXT NOT NULL,
                PRIMARY KEY (source, entry_id),
                FOREIGN KEY (source) REFERENCES archives(source) ON DELETE CASCADE
            );
            CREATE INDEX entries_search ON entries(search_text);
            CREATE INDEX entries_suffix ON entries(suffix);
            CREATE INDEX entries_kind ON entries(kind);
        """)

    @staticmethod
    def _open_existing(path: Path) -> sqlite3.Connection | None:
        if not path.is_file():
            return None
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version != RPF_CATALOG_SCHEMA:
                raise ValueError("Unsupported RPF catalog schema")
            connection.execute("SELECT source FROM archives LIMIT 1").fetchall()
        except (sqlite3.DatabaseError, TypeError) as exc:
            connection.close()
            raise ValueError(f"Invalid RPF catalog: {exc}") from exc
        return connection

    @staticmethod
    def _discover(root: Path) -> tuple[Path, ...]:
        archives: list[Path] = []
        for folder, directories, files in os.walk(root, followlinks=False):
            current = Path(folder)
            safe_directories: list[str] = []
            for directory in directories:
                candidate = current / directory
                if not _is_reparse(candidate):
                    safe_directories.append(directory)
            directories[:] = safe_directories
            for name in files:
                candidate = current / name
                if candidate.suffix.casefold() != ".rpf":
                    continue
                if _is_reparse(candidate) or not candidate.resolve().is_relative_to(root):
                    raise ValueError(f"RPF catalog source contains an unsafe archive: {candidate}")
                archives.append(candidate.resolve())
                if len(archives) > MAX_CATALOG_ARCHIVES:
                    raise ValueError(
                        f"RPF catalog source exceeds {MAX_CATALOG_ARCHIVES} loose archives"
                    )
        folded = [str(path).casefold() for path in archives]
        if len(folded) != len(set(folded)):
            raise ValueError("RPF catalog source has a case-insensitive archive collision")
        return tuple(sorted(archives, key=lambda path: str(path).casefold()))

    def build(
        self, source_root: str | Path, destination: str | Path, *,
        refresh: bool = False, progress: ProgressCallback | None = None,
    ) -> tuple[Path, dict[str, object]]:
        """Create or incrementally refresh an atomic catalog database."""
        root = Path(source_root).expanduser().resolve()
        if not root.is_dir() or _is_reparse(root):
            raise ValueError(f"RPF catalog source must be a real directory: {root}")
        output = Path(destination).expanduser().resolve()
        if output.suffix.casefold() not in {".sqlite", ".db"}:
            raise ValueError("RPF catalog output must use .sqlite or .db")
        if output.is_relative_to(self.gta_path):
            raise ValueError("RPF catalog database must be outside the GTA V installation")
        if output.exists() and not output.is_file():
            raise ValueError(f"RPF catalog output is not a file: {output}")
        output.parent.mkdir(parents=True, exist_ok=True)
        archives = self._discover(root)
        if not archives:
            raise ValueError("RPF catalog source contains no loose .rpf archives")
        old = self._open_existing(output)
        stage_dir = Path(tempfile.mkdtemp(
            prefix=f".{output.stem}.catalog-", dir=output.parent,
        )).resolve()
        staged = stage_dir / output.name
        cached = 0
        indexed = 0
        failed = 0
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(staged)
            connection.execute("PRAGMA foreign_keys = ON")
            self._create_schema(connection)
            connection.executemany(
                "INSERT INTO catalog_meta(key, value) VALUES (?, ?)",
                (
                    ("schema_version", str(RPF_CATALOG_SCHEMA)),
                    ("source_root", str(root)),
                    ("gta_path", str(self.gta_path)),
                    ("created_utc", datetime.now(timezone.utc).isoformat()),
                ),
            )
            total = len(archives)
            for number, archive in enumerate(archives, start=1):
                if progress:
                    progress(f"Cataloging {archive.name}", int((number - 1) * 100 / total))
                source = str(archive)
                relative = archive.relative_to(root).as_posix()
                info = archive.stat()
                cached_row = None
                if old is not None and not refresh:
                    cached_row = old.execute(
                        "SELECT source, relative_path, file_size, mtime_ns, sha256, "
                        "edition, nested_archives, entry_count, error FROM archives "
                        "WHERE source = ? AND file_size = ? AND mtime_ns = ?",
                        (source, info.st_size, info.st_mtime_ns),
                    ).fetchone()
                if cached_row is not None:
                    connection.execute(
                        "INSERT INTO archives VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        cached_row,
                    )
                    rows = old.execute(
                        "SELECT source, entry_id, archive_path, entry_path, name, kind, "
                        "size, stored_size, suffix, resource_version, search_text "
                        "FROM entries WHERE source = ?",
                        (source,),
                    ).fetchall()
                    connection.executemany(
                        "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        rows,
                    )
                    cached += 1
                    continue
                digest_before = _sha256_file(archive)
                try:
                    index = self.explorer.index(archive)
                    digest_after = _sha256_file(archive)
                    if digest_before != digest_after:
                        raise RuntimeError("archive changed while it was being indexed")
                    connection.execute(
                        "INSERT INTO archives VALUES (?, ?, ?, ?, ?, ?, ?, ?, '')",
                        (
                            source, relative, info.st_size, info.st_mtime_ns,
                            digest_before, index.edition, len(index.archives),
                            len(index.entries),
                        ),
                    )
                    connection.executemany(
                        "INSERT INTO entries VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            (
                                source, entry.id, entry.archive_path, entry.path,
                                entry.name, entry.kind, entry.size, entry.stored_size,
                                entry.suffix, entry.resource_version,
                                f"{archive.name} {entry.archive_path} {entry.path}".casefold(),
                            )
                            for entry in index.entries
                        ),
                    )
                    indexed += 1
                except (OSError, RuntimeError, ValueError) as exc:
                    connection.execute(
                        "INSERT INTO archives VALUES (?, ?, ?, ?, ?, '', 0, 0, ?)",
                        (
                            source, relative, info.st_size, info.st_mtime_ns,
                            digest_before, str(exc),
                        ),
                    )
                    failed += 1
            connection.commit()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity != ("ok",):
                raise ValueError("New RPF catalog failed SQLite integrity validation")
            connection.close()
            connection = None
            if old is not None:
                old.close()
                old = None
            staged.replace(output)
            summary: dict[str, object] = {
                "schema_version": RPF_CATALOG_SCHEMA,
                "operation": "rpf_catalog_build",
                "source_root": str(root),
                "database": str(output),
                "archives": len(archives),
                "indexed": indexed,
                "cached": cached,
                "failed": failed,
                "refresh": refresh,
            }
            if progress:
                progress("RPF catalog ready", 100)
            return output, summary
        finally:
            if connection is not None:
                connection.close()
            if old is not None:
                old.close()
            if stage_dir.is_dir() and stage_dir.parent == output.parent:
                shutil.rmtree(stage_dir)

    @classmethod
    def search(
        cls, catalog: str | Path, query: str = "", *, kind: str = "",
        suffix: str = "", limit: int = 250,
    ) -> tuple[RpfCatalogResult, ...]:
        """Search an existing catalog without modifying it."""
        path = Path(catalog).expanduser().resolve()
        connection = cls._open_existing(path)
        if connection is None:
            raise FileNotFoundError(f"RPF catalog not found: {path}")
        try:
            if limit < 1 or limit > MAX_SEARCH_RESULTS:
                raise ValueError(
                    f"RPF catalog search limit must be 1-{MAX_SEARCH_RESULTS:,}"
                )
            wanted_suffix = suffix.strip().casefold()
            if wanted_suffix and not wanted_suffix.startswith("."):
                wanted_suffix = f".{wanted_suffix}"
            parameters: list[object] = [query.strip().casefold()]
            clauses = ["instr(search_text, ?) > 0"]
            if kind:
                clauses.append("kind = ?")
                parameters.append(kind.strip().casefold())
            if wanted_suffix:
                clauses.append("suffix = ?")
                parameters.append(wanted_suffix)
            parameters.append(limit)
            rows = connection.execute(
                "SELECT entries.source, archive_path, entry_path, kind, size, suffix, "
                "archives.edition, resource_version FROM entries JOIN archives "
                "ON entries.source = archives.source WHERE "
                + " AND ".join(clauses)
                + " ORDER BY entry_path COLLATE NOCASE, entries.source COLLATE NOCASE "
                "LIMIT ?",
                parameters,
            ).fetchall()
            return tuple(RpfCatalogResult(*row) for row in rows)
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"Invalid RPF catalog: {exc}") from exc
        finally:
            connection.close()

    @classmethod
    def export_results(
        cls, results: tuple[RpfCatalogResult, ...], destination: str | Path,
        *, query: str,
    ) -> Path:
        output = Path(destination).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(f".{output.name}.tmp")
        temporary.write_text(json.dumps({
            "schema_version": 1,
            "operation": "rpf_catalog_search",
            "query": query,
            "result_count": len(results),
            "results": [asdict(result) for result in results],
        }, indent=2) + "\n", encoding="utf-8")
        temporary.replace(output)
        return output
