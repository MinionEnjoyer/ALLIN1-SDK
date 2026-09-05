"""Reviewed desktop facade for non-destructive RPF archive utilities."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from allin1_sdk.paths import gta_root_containing, project_root
from allin1_sdk.release_paths import no_links, relative_path
from allin1_sdk.rpf_tools import RpfExplorerService


ACTIONS = frozenset({
    "extract_entry", "export_native_workspace", "extract_subtree", "extract_archive",
    "compare", "verify_integrity", "defragment_copy",
})
COMPARISON_MODES = frozenset({"metadata", "logical", "exact"})
_HASH = re.compile(r"[0-9a-f]{64}")


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _file(value: object, label: str, suffix: str | None = None) -> Path:
    if not isinstance(value, str) or not value.strip() or "\0" in value or len(value) > 4096:
        raise ValueError(f"{label} must be a valid path")
    authored = Path(value).expanduser()
    if authored.is_symlink():
        raise ValueError(f"{label} cannot be a symbolic link")
    path = no_links(authored.resolve(strict=True))
    if not path.is_file() or suffix and path.suffix.casefold() != suffix:
        raise ValueError(f"{label} must be a {suffix or 'regular'} file")
    return path


def _directory(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip() or "\0" in value or len(value) > 4096:
        raise ValueError(f"{label} must be a valid path")
    authored = Path(value).expanduser()
    if authored.is_symlink():
        raise ValueError(f"{label} cannot be a symbolic link")
    path = no_links(authored.resolve(strict=True))
    if not path.is_dir():
        raise ValueError(f"{label} must be a directory")
    return path


def _destination(value: object, *, action: str, gta_path: Path) -> tuple[Path, tuple[Path, ...]]:
    if not isinstance(value, str) or not value.strip() or "\0" in value or len(value) > 4096:
        raise ValueError("RPF utility destination must be a valid path")
    authored = Path(value).expanduser()
    if not authored.name or authored.is_symlink():
        raise ValueError("RPF utility destination is invalid")
    relative_path(authored.name)
    parent = no_links(authored.parent.resolve(strict=True))
    if not parent.is_dir():
        raise ValueError("RPF utility destination parent must be a directory")
    destination = no_links(parent / authored.name)
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"RPF utility destination already exists: {destination}")
    if gta_root_containing(destination, explicit_roots=(gta_path,)) is not None:
        raise ValueError("RPF utility outputs cannot be written inside GTA V")
    companions: tuple[Path, ...] = ()
    if action in {"compare", "verify_integrity"} and destination.suffix.casefold() != ".json":
        raise ValueError("RPF report destination must use a .json extension")
    if action == "compare":
        companions = (destination.with_suffix(".md"),)
    elif action == "defragment_copy":
        if destination.suffix.casefold() != ".rpf":
            raise ValueError("Defragmented output must use a .rpf extension")
        companions = (destination.with_name(f"{destination.name}.defragment.json"),)
    for companion in companions:
        no_links(companion)
        if companion.exists() or companion.is_symlink():
            raise ValueError(f"RPF utility companion output already exists: {companion}")
        if gta_root_containing(companion, explicit_roots=(gta_path,)) is not None:
            raise ValueError("RPF utility outputs cannot be written inside GTA V")
    return destination, companions


def _context(payload: dict[str, Any]) -> tuple[RpfExplorerService, Any, dict[str, Any]]:
    action = payload.get("action")
    if action not in ACTIONS:
        raise ValueError("Unsupported RPF utility action")
    archive = _file(payload.get("archive"), "RPF source", ".rpf")
    gta_path = _directory(payload.get("gta_path"), "GTA path")
    destination, companions = _destination(
        payload.get("destination"), action=action, gta_path=gta_path,
    )
    service = RpfExplorerService(project_root(), gta_path)
    index = service.index(archive)
    normalized: dict[str, Any] = {
        "action": action,
        "archive": str(archive),
        "archive_sha256": _sha256(archive),
        "archive_size": archive.stat().st_size,
        "gta_path": str(gta_path),
        "edition": index.edition,
        "destination": str(destination),
        "companion_outputs": [str(item) for item in companions],
    }
    if action in {"extract_entry", "export_native_workspace", "extract_subtree"}:
        entry_id = payload.get("entry_id")
        if not isinstance(entry_id, str) or not entry_id or "\0" in entry_id or len(entry_id) > 2048:
            raise ValueError("RPF extraction requires one exact entry identity")
        entry = index.entry(entry_id)
        if action == "extract_entry" and entry.kind == "directory":
            raise ValueError("A directory must be exported as a subtree")
        if action == "export_native_workspace" and Path(entry.name).suffix.casefold() not in {".ydr", ".ydd", ".yft", ".ytd"}:
            raise ValueError("Editable native export requires a YDR, YDD, YFT, or YTD entry")
        if action == "extract_subtree" and entry.kind != "directory":
            raise ValueError("Subtree export requires a directory entry")
        normalized["entry"] = {
            "id": entry.id, "archive_path": entry.archive_path,
            "path": entry.path, "kind": entry.kind, "size": entry.size,
        }
    if action == "compare":
        other = _file(payload.get("compare_archive"), "Comparison RPF", ".rpf")
        if other == archive:
            raise ValueError("Select a different RPF archive to compare")
        mode = payload.get("comparison_mode", "logical")
        if mode not in COMPARISON_MODES:
            raise ValueError("RPF comparison mode must be metadata, logical, or exact")
        other_index = service.index(other)
        normalized.update({
            "compare_archive": str(other), "compare_archive_sha256": _sha256(other),
            "compare_archive_size": other.stat().st_size, "comparison_mode": mode,
            "compare_edition": other_index.edition,
        })
    return service, index, normalized


def _review_value(payload: dict[str, Any]) -> tuple[RpfExplorerService, Any, dict[str, Any]]:
    service, index, normalized = _context(payload)
    labels = {
        "extract_entry": "Extract exact member",
        "export_native_workspace": "Export editable native workspace",
        "extract_subtree": "Export selected subtree",
        "extract_archive": "Export complete archive tree",
        "compare": "Compare recursive archives",
        "verify_integrity": "Verify every recursive payload",
        "defragment_copy": "Build verified defragmented copy",
    }
    review = {
        "kind": "rpf_utility_review", "operation": "review_rpf_utility",
        **normalized, "label": labels[normalized["action"]],
        "source_archive_count": len(index.archives),
        "source_entry_count": len(index.entries),
        "ready": True, "review_only": True, "output_write_performed": False,
        "source_write_performed": False, "game_write_performed": False,
    }
    review["review_sha256"] = hashlib.sha256(json.dumps(
        review, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")).hexdigest()
    return service, index, review


def review(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("RPF utility review payload must be an object")
    _service, _index, result = _review_value(payload)
    return result


def apply(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("RPF utility apply payload must be an object")
    digest = payload.get("review_sha256")
    if not isinstance(digest, str) or _HASH.fullmatch(digest) is None:
        raise ValueError("RPF utility apply requires reviewed evidence")
    if payload.get("authoring_confirmed") is not True:
        raise ValueError("RPF utility output requires action-time confirmation")
    service, index, current = _review_value(payload)
    if current["review_sha256"] != digest:
        raise ValueError("RPF utility source or output changed after review")
    action = current["action"]
    destination = Path(current["destination"])
    outputs = (destination, *(Path(item) for item in current["companion_outputs"]))
    try:
        if action == "extract_entry":
            output = service.extract(index, index.entry(current["entry"]["id"]), destination)
            evidence: object = {"file": str(output), "sha256": _sha256(output), "bytes": output.stat().st_size}
        elif action == "export_native_workspace":
            output = service.export_native_workspace(
                index, index.entry(current["entry"]["id"]), destination,
            )
            manifest = output / "native-workspace.json"
            evidence = {
                "directory": str(output), "manifest": str(manifest),
                "manifest_sha256": _sha256(manifest), "entry_name": current["entry"]["path"],
            }
        elif action in {"extract_subtree", "extract_archive"}:
            entry = current.get("entry", {})
            output = service.extract_subtree(
                index, destination,
                archive_path=str(entry.get("archive_path", "")),
                directory_path=str(entry.get("path", "")),
            )
            manifest = output / ".allin1-rpf-export.json"
            evidence = {
                "directory": str(output), "manifest": str(manifest),
                "manifest_sha256": _sha256(manifest),
            }
        elif action == "compare":
            other = service.index(current["compare_archive"])
            mode = current["comparison_mode"]
            report = service.compare_indexes(
                index, other, exact_content=mode == "exact", logical_content=mode == "logical",
            )
            json_path, markdown_path = service.export_diff(report, destination)
            evidence = {
                "summary": report["summary"], "json": str(json_path),
                "json_sha256": _sha256(json_path), "markdown": str(markdown_path),
                "markdown_sha256": _sha256(markdown_path),
            }
        elif action == "verify_integrity":
            report_path, report = service.verify_archive_integrity(index, destination)
            evidence = {
                "status": report["status"], "summary": report["summary"],
                "report": str(report_path), "report_sha256": _sha256(report_path),
            }
        else:
            report_path = Path(current["companion_outputs"][0])
            output, written_report, report = service.defragment_verified_copy(
                index, destination, report_path,
            )
            evidence = {
                "archive": str(output), "archive_sha256": _sha256(output),
                "report": str(written_report), "report_sha256": _sha256(written_report),
                "summary": report["summary"],
            }
        if _sha256(Path(current["archive"])) != current["archive_sha256"]:
            raise RuntimeError("RPF source changed while producing the reviewed output")
        if action == "compare" and _sha256(Path(current["compare_archive"])) != current["compare_archive_sha256"]:
            raise RuntimeError("Comparison RPF changed while producing the reviewed output")
    except Exception:
        for created in reversed(outputs):
            if created.is_symlink():
                created.unlink(missing_ok=True)
            elif created.is_dir():
                no_links(created)
                shutil.rmtree(created)
            elif created.exists():
                no_links(created).unlink()
        raise
    return {
        "kind": "rpf_utility_result", "operation": "apply_rpf_utility",
        "action": action, "label": current["label"], "archive": current["archive"],
        "archive_sha256": current["archive_sha256"], "destination": str(destination),
        "review_sha256": digest, "evidence": evidence,
        "output_write_performed": True, "source_write_performed": False,
        "game_write_performed": False,
    }
