"""Derive guarded RPF change plans from known-good before/after archives."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from allin1_sdk.rpf_tools import (
    ProgressCallback,
    RpfEntryRecord,
    RpfExplorerService,
    RpfIndex,
    _sha256_file,
)


@dataclass(frozen=True)
class RpfDeltaPlanResult:
    """Published plan and its optional portable payload sidecar."""

    plan_path: Path
    payload_directory: Path | None
    plan: dict[str, Any]
    diff: dict[str, Any]


def _emit(progress: ProgressCallback | None, message: str, percent: int) -> None:
    if progress is not None:
        progress(message, percent)


def _entry_key(entry: RpfEntryRecord) -> tuple[str, str]:
    return entry.archive_path.casefold(), entry.path.casefold()


def _container_path(entry: RpfEntryRecord) -> str:
    return (
        entry.path if not entry.archive_path
        else f"{entry.archive_path}!{entry.path}"
    )


def _inside_container(archive_path: str, containers: set[str]) -> bool:
    folded = archive_path.casefold()
    return any(folded == item or folded.startswith(item + "!") for item in containers)


def _path_depth(path: str) -> int:
    return len(PurePosixPath(path).parts)


def _archive_depth(path: str) -> int:
    return 0 if not path else len(path.split("!"))


def _payload_suffix(entry: RpfEntryRecord) -> str:
    suffix = PurePosixPath(entry.name).suffix.casefold()
    if (
        len(suffix) <= 1 or len(suffix) > 16
        or any(
            not (character.isascii() and character.isalnum())
            for character in suffix[1:]
        )
    ):
        return ".bin"
    return suffix


def _write_json_new(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def derive_rpf_change_plan(
    service: RpfExplorerService,
    base: RpfIndex,
    desired: RpfIndex,
    destination: str | Path,
    *,
    exact_content: bool = False,
    progress: ProgressCallback | None = None,
) -> RpfDeltaPlanResult:
    """Convert a recursive before/after diff into one portable guarded plan.

    The base archive is the transaction target. The desired archive is read-only and
    supplies only the payloads whose canonical content changed. Added nested archives
    are kept as single container payloads; existing nested archives are edited through
    their deep virtual paths instead of being needlessly replaced wholesale.
    """
    if base.edition.casefold() != desired.edition.casefold():
        raise ValueError(
            "RPF delta planning requires matching GTA V editions; convert or rebuild "
            "the desired archive for the base edition first"
        )
    authored_output = Path(destination).expanduser()
    if authored_output.is_symlink():
        raise ValueError("RPF delta plan output cannot be a symbolic link")
    output = authored_output.resolve()
    if output.suffix.casefold() != ".json":
        raise ValueError("RPF delta plan output must use a .json extension")
    if output.exists() or output.is_symlink():
        raise ValueError(f"RPF delta plan output already exists: {output}")
    payload_directory = output.with_name(f"{output.stem}.payloads")
    if payload_directory.exists() or payload_directory.is_symlink():
        raise ValueError(
            f"RPF delta payload directory already exists: {payload_directory}"
        )
    if output.is_relative_to(service.gta_path) or payload_directory.is_relative_to(
        service.gta_path
    ):
        raise ValueError(
            "RPF delta plans and payloads must be written outside the GTA V installation"
        )

    base_entries = {_entry_key(entry): entry for entry in base.entries}
    desired_entries = {_entry_key(entry): entry for entry in desired.entries}
    for key in base_entries.keys() & desired_entries.keys():
        before, after = base_entries[key], desired_entries[key]
        if before.archive_path != after.archive_path or before.path != after.path:
            raise ValueError(
                "RPF delta contains a case-only path change that cannot be represented "
                f"safely: {before.virtual_name} -> {after.virtual_name}"
            )
        if before.kind != after.kind:
            raise ValueError(
                "RPF delta changes an entry type in place; review it as explicit delete "
                f"and add operations: {after.virtual_name} ({before.kind} -> {after.kind})"
            )

    output.parent.mkdir(parents=True, exist_ok=True)
    _emit(progress, "Comparing canonical recursive archive content", 8)
    diff = service.compare_indexes(
        base, desired, exact_content=exact_content,
        logical_content=not exact_content,
    )
    base_hash = str(diff["left"]["sha256"])
    desired_hash = str(diff["right"]["sha256"])
    added_keys = desired_entries.keys() - base_entries.keys()
    removed_keys = base_entries.keys() - desired_entries.keys()
    modified_items = {
        (
            str(item["identity"]["archive_path"]).casefold(),
            str(item["identity"]["path"]).casefold(),
        ): item
        for item in diff["entries"]["modified"]
    }

    added_containers = {
        _container_path(desired_entries[key]).casefold()
        for key in added_keys if desired_entries[key].kind == "archive"
    }
    removed_containers = {
        _container_path(base_entries[key]).casefold()
        for key in removed_keys if base_entries[key].kind == "archive"
    }

    mkdir_entries = [
        desired_entries[key] for key in added_keys
        if desired_entries[key].kind == "directory"
        and not _inside_container(desired_entries[key].archive_path, added_containers)
    ]
    payload_entries: list[tuple[str, RpfEntryRecord]] = []
    for key in added_keys:
        entry = desired_entries[key]
        if entry.kind == "directory" or _inside_container(
            entry.archive_path, added_containers
        ):
            continue
        payload_entries.append(("add", entry))
    for key, item in modified_items.items():
        entry = desired_entries[key]
        if entry.kind in {"directory", "archive"}:
            continue
        changed_fields = set(item["changes"])
        content_field = "sha256" if exact_content else "logical_content"
        if content_field in changed_fields:
            payload_entries.append(("replace", entry))

    delete_entries = [
        base_entries[key] for key in removed_keys
        if base_entries[key].kind != "directory"
        and not _inside_container(base_entries[key].archive_path, removed_containers)
    ]
    rmdir_entries = [
        base_entries[key] for key in removed_keys
        if base_entries[key].kind == "directory"
        and not _inside_container(base_entries[key].archive_path, removed_containers)
    ]

    mkdir_entries.sort(
        key=lambda item: (
            _archive_depth(item.archive_path), _path_depth(item.path),
            item.id.casefold(),
        )
    )
    payload_entries.sort(
        key=lambda item: (item[0] != "replace", item[1].id.casefold())
    )
    delete_entries.sort(
        key=lambda item: (
            -_archive_depth(item.archive_path), -_path_depth(item.path),
            item.id.casefold(),
        )
    )
    rmdir_entries.sort(
        key=lambda item: (
            -_archive_depth(item.archive_path), -_path_depth(item.path),
            item.id.casefold(),
        )
    )
    total_actions = (
        len(mkdir_entries) + len(payload_entries)
        + len(delete_entries) + len(rmdir_entries)
    )
    if total_actions == 0:
        raise ValueError(
            "The archives contain no actionable logical payload or tree changes; "
            "container packing and archive metadata differences were intentionally ignored"
        )

    staged_directory: Path | None = None
    published_payloads = False
    authored_changes: list[dict[str, Any]] = [
        {"action": "mkdir", "archive_path": entry.archive_path, "entry": entry.path}
        for entry in mkdir_entries
    ]
    try:
        if payload_entries:
            staged_directory = Path(tempfile.mkdtemp(
                prefix=f".{payload_directory.name}.staging-", dir=output.parent,
            )).resolve()
            for number, (action, entry) in enumerate(payload_entries, start=1):
                percent = 18 + int((number / len(payload_entries)) * 42)
                _emit(
                    progress,
                    f"Extracting desired payload {number:,} of {len(payload_entries):,}",
                    percent,
                )
                filename = f"{number:04d}{_payload_suffix(entry)}"
                staged = service.extract(desired, entry, staged_directory / filename)
                authored_changes.append({
                    "action": action,
                    "archive_path": entry.archive_path,
                    "entry": entry.path,
                    "payload": payload_directory / filename,
                })
                if not staged.is_file():
                    raise RuntimeError(
                        f"Desired RPF payload was not extracted: {entry.id}"
                    )
            staged_directory.replace(payload_directory)
            staged_directory = None
            published_payloads = True

        authored_changes.extend(
            {"action": "delete", "archive_path": entry.archive_path, "entry": entry.path}
            for entry in delete_entries
        )
        authored_changes.extend(
            {"action": "rmdir", "archive_path": entry.archive_path, "entry": entry.path}
            for entry in rmdir_entries
        )
        _emit(progress, "Binding every action and payload to the base archive", 68)
        plan = service.multi_change_plan(base, authored_changes)
        if _sha256_file(base.source) != base_hash:
            raise RuntimeError(
                "Base RPF changed during delta planning; output was discarded"
            )
        if _sha256_file(desired.source) != desired_hash:
            raise RuntimeError(
                "Desired RPF changed during delta planning; output was discarded"
            )

        # The guarded plan identity binds payload hashes rather than machine-specific
        # paths. Keep derived sidecars relocatable with the plan; apply resolves these
        # paths only inside the declared sibling directory.
        for change in plan["changes"]:
            payload = change.get("payload")
            if payload is not None:
                payload["path"] = str(
                    Path(payload_directory.name) / Path(payload["path"]).name
                )

        action_counts = Counter(change["action"] for change in plan["changes"])
        plan["derived_delta"] = {
            "schema_version": 1,
            "operation": "rpf_before_after_delta",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "comparison_mode": diff["comparison_mode"],
            "base": diff["left"],
            "desired": diff["right"],
            "diff_summary": diff["summary"],
            "action_count": len(plan["changes"]),
            "action_counts": dict(sorted(action_counts.items())),
            "payload_count": len(payload_entries),
            "payload_directory": payload_directory.name if payload_entries else None,
            "container_repacking_ignored": True,
            "source_archives_unchanged": True,
        }
        _emit(progress, "Publishing portable reviewed delta plan", 90)
        _write_json_new(output, plan)
        _emit(progress, "RPF delta plan ready", 100)
        return RpfDeltaPlanResult(
            plan_path=output,
            payload_directory=payload_directory if payload_entries else None,
            plan=plan,
            diff=diff,
        )
    except Exception:
        output.unlink(missing_ok=True)
        if staged_directory is not None and staged_directory.is_dir():
            shutil.rmtree(staged_directory)
        if published_payloads and payload_directory.is_dir():
            shutil.rmtree(payload_directory)
        raise
