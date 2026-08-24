"""Shared safety primitives for copied, structured package authoring.

The specialist workbenches retain ownership of their metadata contracts.  This
module owns the invariants that should not vary between them: source isolation,
bounded copies, safe XML parsing, complete pre-edit snapshots, staged multi-file
commits, exact restores, and atomic workspace-manifest writes.
"""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from lxml import etree

from allin1_sdk.addon_importer import (
    MAX_PACKAGE_BYTES,
    PackageAssetReader,
    PackageScan,
)


MAX_AUTHORING_MEMBER_BYTES = 512 * 1024 * 1024
MAX_AUTHORING_XML_BYTES = 16 * 1024 * 1024


def safe_xml_parser() -> etree.XMLParser:
    """Return the non-networked parser used for every authored XML document."""
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        recover=False,
        huge_tree=False,
        remove_blank_text=False,
        remove_comments=False,
    )


def safe_relative_path(value: str, *, label: str = "authoring member") -> PurePosixPath:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or ":" in path.parts[0]
        or any(ord(character) < 32 for character in normalized)
    ):
        raise ValueError(f"Unsafe {label}: {value}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def copy_package_scan(source: Path, scan: PackageScan, target: Path) -> str:
    """Copy every inspected member without following package-owned links."""
    reader = PackageAssetReader(source)
    copied_bytes = 0
    inventory = hashlib.sha256()
    target = target.resolve()
    for entry in sorted(scan.entries, key=lambda item: item.path.casefold()):
        if entry.size > MAX_AUTHORING_MEMBER_BYTES:
            raise ValueError(
                f"Authoring member exceeds the guarded 512 MiB limit: {entry.path}"
            )
        relative = safe_relative_path(entry.path)
        destination = (target / Path(*relative.parts)).resolve(strict=False)
        if not destination.is_relative_to(target):
            raise ValueError(f"Authoring member escapes the workspace: {entry.path}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            unresolved = source / Path(*relative.parts)
            original = unresolved.resolve(strict=True)
            if (
                unresolved.is_symlink()
                or original.is_symlink()
                or not original.is_relative_to(source)
                or not original.is_file()
            ):
                raise ValueError(f"Unsafe authoring source member: {entry.path}")
            shutil.copyfile(original, destination)
            source_hash = _sha256(original)
        else:
            content = reader.read(entry.path, limit=entry.size + 1)
            if content.truncated or len(content.data) != entry.size:
                raise ValueError(f"Could not copy complete authoring member: {entry.path}")
            destination.write_bytes(content.data)
            source_hash = hashlib.sha256(content.data).hexdigest()
        if destination.stat().st_size != entry.size:
            raise RuntimeError(f"Authoring copy size mismatch: {entry.path}")
        if _sha256(destination) != source_hash:
            raise RuntimeError(f"Authoring copy hash mismatch: {entry.path}")
        inventory.update(entry.path.casefold().encode("utf-8"))
        inventory.update(b"\0")
        inventory.update(str(entry.size).encode("ascii"))
        inventory.update(b"\0")
        inventory.update(source_hash.encode("ascii"))
        inventory.update(b"\n")
        copied_bytes += entry.size
        if copied_bytes > MAX_PACKAGE_BYTES:
            raise ValueError("Copied authoring source exceeds the package size limit")
    return inventory.hexdigest()


def create_copied_workspace(
    source: Path,
    destination: Path,
    scan: PackageScan,
    *,
    manifest_name: str,
    manifest: dict[str, Any],
    validation_name: str,
    validate_copy: Callable[[Path], dict[str, Any]],
) -> Path:
    """Publish a copied workspace only after the copied tree reparses cleanly."""
    source = source.resolve()
    target = destination.resolve()
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"Authoring destination already exists: {target}")
    if target == source or target.is_relative_to(source):
        raise ValueError("Authoring output must be outside its source tree")
    target.parent.mkdir(parents=True, exist_ok=True)
    required = scan.total_bytes + 64 * 1024 * 1024
    if shutil.disk_usage(target.parent).free < required:
        raise ValueError("Not enough free disk space for the copied authoring workspace")
    stage = Path(tempfile.mkdtemp(
        prefix=f".{target.name}.authoring-", dir=target.parent,
    )).resolve()
    try:
        relative_root = (
            PurePosixPath("source/dlc.rpf.source")
            if source.is_dir() and source.name.casefold() == "dlc.rpf.source"
            else PurePosixPath("source")
        )
        content_root = stage / Path(*relative_root.parts)
        content_root.mkdir(parents=True)
        content_fingerprint = copy_package_scan(source, scan, content_root)
        report = validate_copy(content_root)
        manifest = dict(manifest)
        manifest["content_root"] = relative_root.as_posix()
        manifest["source_content_fingerprint"] = content_fingerprint
        (stage / "history").mkdir()
        (stage / "reports").mkdir()
        (stage / manifest_name).write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
        )
        (stage / "reports" / validation_name).write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8",
        )
        stage.rename(target)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return target


class GuardedXmlWorkspace:
    """Filesystem transaction layer shared by structured authoring domains."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        manifest_name: str,
        operation: str,
        schema_version: int,
        subject_label: str,
    ) -> None:
        unresolved = Path(workspace).expanduser()
        if unresolved.is_symlink():
            raise ValueError(f"{subject_label} workspace cannot be a symbolic link")
        self.root = unresolved.resolve()
        self.manifest_path = self.root / manifest_name
        self.operation = operation
        self.subject_label = subject_label
        self.schema_version = schema_version
        if not self.root.is_dir() or not self.manifest_path.is_file():
            raise ValueError(f"{subject_label} workspace manifest is missing")
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid {subject_label} authoring manifest: {exc}") from exc
        if not isinstance(manifest, dict):
            raise ValueError(f"Invalid {subject_label} authoring manifest")
        if manifest.get("schema_version") != schema_version:
            raise ValueError(f"Unsupported {subject_label} authoring workspace schema")
        if manifest.get("operation") != operation:
            raise ValueError(f"Unexpected {subject_label} authoring workspace operation")
        relative = manifest.get("content_root")
        if not isinstance(relative, str):
            raise ValueError(f"{subject_label} authoring workspace has no content root")
        path = safe_relative_path(relative, label="authoring content root")
        source = (self.root / Path(*path.parts)).resolve()
        if not source.is_relative_to(self.root) or not source.is_dir() or source.is_symlink():
            raise ValueError(f"{subject_label} authoring content root is missing or unsafe")
        history = self.root / "history"
        if not history.is_dir() or history.is_symlink():
            raise ValueError(f"{subject_label} authoring history root is missing or unsafe")
        self.source = source
        self.manifest: dict[str, Any] = manifest

    def refresh_manifest(self) -> None:
        """Refresh cached state while refusing a manifest that changes workspace roots."""
        try:
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"Invalid {self.subject_label} authoring manifest: {exc}"
            ) from exc
        if not isinstance(manifest, dict):
            raise ValueError(f"Invalid {self.subject_label} authoring manifest")
        if manifest.get("schema_version") != self.schema_version:
            raise ValueError(f"Unsupported {self.subject_label} authoring workspace schema")
        if manifest.get("operation") != self.operation:
            raise ValueError(f"Unexpected {self.subject_label} authoring workspace operation")
        relative = manifest.get("content_root")
        if not isinstance(relative, str):
            raise ValueError(f"{self.subject_label} authoring workspace has no content root")
        path = safe_relative_path(relative, label="authoring content root")
        source = (self.root / Path(*path.parts)).resolve()
        if source != self.source or not source.is_dir() or source.is_symlink():
            raise ValueError(
                f"{self.subject_label} authoring content root changed or became unsafe"
            )
        self.manifest.clear()
        self.manifest.update(manifest)

    @contextmanager
    def operation_lock(self):
        """Serialize mutations from multiple UI/CLI processes for this workspace."""
        lock_path = self.root / ".authoring.lock"
        with lock_path.open("a+b") as stream:
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @property
    def revision(self) -> int:
        value = self.manifest.get("revision", 0)
        if not isinstance(value, int) or value < 0:
            raise ValueError(f"{self.subject_label} authoring revision is invalid")
        return value

    def member(self, relative: str) -> Path:
        path = safe_relative_path(relative, label=f"{self.subject_label} authoring member")
        unresolved = self.source / Path(*path.parts)
        candidate = unresolved.resolve(strict=True)
        if (
            unresolved.is_symlink()
            or candidate.is_symlink()
            or not candidate.is_relative_to(self.source)
            or not candidate.is_file()
        ):
            raise ValueError(f"Unsafe {self.subject_label} authoring member: {relative}")
        return candidate

    def destination(self, relative: str) -> Path:
        path = safe_relative_path(
            relative, label=f"{self.subject_label} authoring destination",
        )
        candidate = (self.source / Path(*path.parts)).resolve(strict=False)
        if not candidate.is_relative_to(self.source):
            raise ValueError(f"Unsafe {self.subject_label} authoring destination: {relative}")
        return candidate

    def read_tree(self, relative: str) -> etree._ElementTree:
        path = self.member(relative)
        size = path.stat().st_size
        if not 0 < size <= MAX_AUTHORING_XML_BYTES:
            raise ValueError(f"Authoring XML is empty or exceeds 16 MiB: {relative}")
        try:
            tree = etree.parse(str(path), safe_xml_parser())
        except (OSError, etree.XMLSyntaxError) as exc:
            raise ValueError(f"Invalid authoring XML {relative}: {exc}") from exc
        if tree.docinfo.doctype:
            raise ValueError(f"Authoring XML contains a prohibited document type: {relative}")
        return tree

    def snapshot(
        self,
        subject: str,
        files: tuple[str, ...],
        changes: tuple[dict[str, str], ...],
        *,
        operation: str,
    ) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        history = self.root / "history" / f"{stamp}-edit"
        history.mkdir()
        backups = history / "files"
        backups.mkdir()
        paths = tuple(dict.fromkeys(files))
        try:
            hashes: dict[str, str] = {}
            for relative in sorted(paths, key=str.casefold):
                source = self.member(relative)
                target = backups / Path(*safe_relative_path(relative).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                hashes[relative] = _sha256(target)
            record = {
                "operation": operation,
                "subject": subject,
                "revision_before": self.revision,
                "files": sorted(paths, key=str.casefold),
                "sha256": hashes,
                "changes": list(changes),
            }
            (history / "edit.json").write_text(
                json.dumps(record, indent=2) + "\n", encoding="utf-8",
            )
        except Exception:
            shutil.rmtree(history, ignore_errors=True)
            raise
        return history

    def restore(self, history: Path) -> None:
        record = self.history_record(history)
        files = record.get("files")
        hashes = record.get("sha256")
        if not isinstance(files, list):
            raise ValueError(f"{self.subject_label} authoring history has invalid files")
        if not isinstance(hashes, dict):
            raise ValueError(f"{self.subject_label} authoring history has invalid hashes")
        staged: dict[str, Path] = {}
        try:
            for value in files:
                if not isinstance(value, str):
                    raise ValueError(
                        f"{self.subject_label} authoring history contains an invalid path"
                    )
                relative = safe_relative_path(value, label="authoring history member")
                backup = history / "files" / Path(*relative.parts)
                if not backup.is_file() or backup.is_symlink():
                    raise ValueError(
                        f"{self.subject_label} authoring backup is missing: {value}"
                    )
                expected_hash = hashes.get(value)
                if not isinstance(expected_hash, str) or _sha256(backup) != expected_hash:
                    raise ValueError(
                        f"{self.subject_label} authoring backup hash is invalid: {value}"
                    )
                destination = self.destination(value)
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.with_name(
                    f".{destination.name}.{stamp_token()}.restore.tmp"
                )
                shutil.copyfile(backup, temporary)
                staged[value] = temporary
            for relative, temporary in staged.items():
                temporary.replace(self.destination(relative))
        finally:
            for temporary in staged.values():
                temporary.unlink(missing_ok=True)

    def record_post_edit_state(self, history: Path) -> None:
        """Bind an undo record to the exact files produced by its edit.

        The pre-edit hashes protect the retained backups.  These post-edit
        hashes serve a different purpose: they keep a later undo from silently
        overwriting changes made outside the authoring workspace after the SDK
        edit completed.
        """
        record = self.history_record(history)
        files = record.get("files")
        if not isinstance(files, list) or not all(
            isinstance(item, str) for item in files
        ):
            raise ValueError(
                f"{self.subject_label} authoring history has invalid files"
            )
        record["sha256_after"] = {
            relative: _sha256(self.member(relative))
            for relative in sorted(files, key=str.casefold)
        }
        self._write_history_record(history, record)

    def verify_post_edit_state(self, history: Path) -> None:
        """Refuse undo when any edited member changed after the SDK edit."""
        record = self.history_record(history)
        files = record.get("files")
        hashes = record.get("sha256_after")
        if not isinstance(files, list) or not all(
            isinstance(item, str) for item in files
        ):
            raise ValueError(
                f"{self.subject_label} authoring history has invalid files"
            )
        if not isinstance(hashes, dict) or set(hashes) != set(files):
            raise ValueError(
                f"{self.subject_label} authoring history has no verified "
                "post-edit state"
            )
        for relative in files:
            expected = hashes.get(relative)
            if not isinstance(expected, str):
                raise ValueError(
                    f"{self.subject_label} authoring post-edit hash is invalid: "
                    f"{relative}"
                )
            try:
                current = _sha256(self.member(relative))
            except (OSError, ValueError) as exc:
                raise ValueError(
                    f"{self.subject_label} authoring member changed after its "
                    f"edit: {relative}"
                ) from exc
            if current != expected:
                raise ValueError(
                    f"{self.subject_label} authoring member changed after its "
                    f"edit: {relative}"
                )

    def commit_trees(self, trees: dict[str, etree._ElementTree]) -> None:
        staged: dict[str, Path] = {}
        try:
            for relative, tree in trees.items():
                destination = self.member(relative)
                temporary = destination.with_name(
                    f".{destination.name}.{stamp_token()}.authoring.tmp"
                )
                tree.write(
                    str(temporary),
                    encoding="utf-8",
                    xml_declaration=True,
                    pretty_print=False,
                )
                etree.parse(str(temporary), safe_xml_parser())
                staged[relative] = temporary
            for relative, temporary in staged.items():
                temporary.replace(self.member(relative))
        finally:
            for temporary in staged.values():
                temporary.unlink(missing_ok=True)

    def latest_history(self) -> Path:
        candidates = sorted(
            (
                path for path in (self.root / "history").iterdir()
                if path.is_dir()
                and not path.is_symlink()
                and (path / "edit.json").is_file()
                and not path.name.endswith(".undone")
                and not path.name.endswith(".undo-recovery")
            ),
            key=lambda path: path.name,
            reverse=True,
        )
        if not candidates:
            raise ValueError(f"{self.subject_label} authoring workspace has no edit to undo")
        return candidates[0]

    def snapshot_current_for_undo(self, original: Path) -> Path:
        record = self.history_record(original)
        files = record.get("files")
        if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
            raise ValueError(f"{self.subject_label} authoring history has invalid files")
        snapshot = self.snapshot(
            str(record.get("subject", "")),
            tuple(files),
            (),
            operation=f"{self.operation}_undo_recovery",
        )
        recovery = snapshot.with_name(f"{snapshot.name}.undo-recovery")
        snapshot.rename(recovery)
        return recovery

    def history_record(self, history: Path) -> dict[str, Any]:
        if history.parent != self.root / "history" or history.is_symlink():
            raise ValueError(f"Unsafe {self.subject_label} authoring history directory")
        try:
            value = json.loads((history / "edit.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid {self.subject_label} authoring history: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"Invalid {self.subject_label} authoring history")
        return value

    def _write_history_record(
        self, history: Path, record: dict[str, Any],
    ) -> None:
        # Reuse history_record's boundary checks before replacing edit.json.
        self.history_record(history)
        destination = history / "edit.json"
        temporary = destination.with_name(
            f".{destination.name}.{stamp_token()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(record, indent=2) + "\n", encoding="utf-8",
            )
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def write_manifest(self) -> None:
        temporary = self.manifest_path.with_name(
            f".{self.manifest_path.name}.{stamp_token()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(self.manifest, indent=2) + "\n", encoding="utf-8",
            )
            temporary.replace(self.manifest_path)
        finally:
            temporary.unlink(missing_ok=True)


def stamp_token() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


__all__ = [
    "GuardedXmlWorkspace",
    "MAX_AUTHORING_MEMBER_BYTES",
    "MAX_AUTHORING_XML_BYTES",
    "copy_package_scan",
    "create_copied_workspace",
    "safe_relative_path",
    "safe_xml_parser",
]
