"""Build a new, verified RPF copy from one archive-bound text workspace."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from allin1_sdk.gxt2_workspace import Gxt2Workspace
from allin1_sdk.paths import project_root
from allin1_sdk.rpf_tools import RpfExplorerService

MAX_ARCHIVE_BYTES = 16 * 1024**3
MAX_ENTRY_BYTES = 512 * 1024**2
MAX_ENTRIES = 25_000
MARGIN_BYTES = 64 * 1024**2


def _hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _write_json(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")


def _source(state):
    from allin1_sdk.gxt2_desktop import _path
    binding = state["source_binding"]
    if not binding or not binding.get("gta_path"):
        raise ValueError("RPF packaging requires a copied archive dictionary with GTA decoding context")
    archive, game = _path(binding["outer_archive"]), _path(binding["gta_path"])
    if not game.is_dir() or not archive.is_file() or archive.suffix.casefold() != ".rpf":
        raise ValueError("The original RPF archive and matching GTA folder must be available")
    if not 0 < archive.stat().st_size <= MAX_ARCHIVE_BYTES:
        raise ValueError("RPF packaging is limited to 16 GiB archives")
    if _hash(archive) != binding["outer_archive_sha256"]:
        raise ValueError("Original RPF changed since dictionary intake; reopen the source before packaging")
    return archive, game, binding


def _shape(index):
    return {
        "entries": sorted((e.id, e.kind, e.resource_version) for e in index.entries),
        "archives": sorted((a.path, a.version) for a in index.archives),
    }


def _verification_entries(index, entry):
    # Parent containers necessarily change when their child is replaced. Every
    # other payload, including unrelated opaque archive members, is compared.
    ancestors, container = set(), ""
    for path in RpfExplorerService._nested_archive_chain(entry.archive_path):
        ancestors.add(f"{container}::{path}")
        container = f"{container}!{path}" if container else path
    return tuple(e for e in index.entries if e.kind != "directory" and e.id not in ancestors)


def review(root, entries, state, destination):
    if os.name != "nt":
        raise ValueError("RPF package publication currently requires Windows exclusive directory rename")
    archive, game, binding = _source(state)
    if destination.is_relative_to(game):
        raise ValueError("RPF packages must be outside the selected GTA folder")
    service = RpfExplorerService(project_root(), game)
    service._require_game_closed()
    index = service.index(archive)
    if len(index.entries) > MAX_ENTRIES or len(index.archives) > MAX_ENTRIES:
        raise ValueError("RPF packaging exceeds the 25,000-entry verification limit")
    try:
        entry = index.entry(binding["entry_id"])
    except KeyError as exc:
        raise ValueError("Bound GXT2 archive member is missing") from exc
    service._require_supported_nested_archive(index, entry.archive_path)
    original, actual_binding = service.read_gxt2_entry(index, entry)
    if any(actual_binding[k] != binding[k] for k in actual_binding) or hashlib.sha256(original).hexdigest() != state["source_sha256"]:
        raise ValueError("Original dictionary or archive edition does not match workspace provenance")
    encoded = Gxt2Workspace.encode(entries)
    payload_hash = hashlib.sha256(encoded).hexdigest()
    if payload_hash == state["source_sha256"]:
        raise ValueError("Dictionary is unchanged; save a text edit before packaging an RPF")
    selected = _verification_entries(index, entry)
    logical_bytes = sum(e.size for e in selected)
    if logical_bytes > MAX_ARCHIVE_BYTES or any(e.size > MAX_ENTRY_BYTES for e in selected):
        raise ValueError("RPF payload verification exceeds the 16 GiB total / 512 MiB member limit")
    required = archive.stat().st_size * 4 + logical_bytes + len(encoded) * 3 + MARGIN_BYTES
    if shutil.disk_usage(destination.parent).free < required:
        raise ValueError("Not enough output disk space for RPF copies, rollback and verification")
    if shutil.disk_usage(tempfile.gettempdir()).free < logical_bytes + MARGIN_BYTES:
        raise ValueError("Not enough temporary disk space for RPF payload verification")
    if _hash(archive) != binding["outer_archive_sha256"]:
        raise ValueError("Original RPF changed during package review")
    # Relative layout keeps the original filename (NG keys can depend on it).
    return {
        "archive_name": archive.name, "archive_size": archive.stat().st_size,
        "entry_id": entry.id, "entry_size_before": len(original), "entry_size_after": len(encoded),
        "payload_sha256": payload_hash, "original_sha256": state["source_sha256"],
        "archive_sha256": binding["outer_archive_sha256"], "edition": index.edition,
        "index_sha256": hashlib.sha256(json.dumps(_shape(index), sort_keys=True).encode()).hexdigest(),
        "indexed_entries": len(index.entries), "verified_payloads": len(selected),
        "required_free_bytes": required, "game_must_be_closed": True,
        "outputs": [f"archive/{archive.name}", "payload/replacement.gxt2", "payload/replacement.gxt2.gxt2-validation.json", "rpf-package.json"],
        "source_unchanged_required": True, "new_output_only": True,
    }


def build(root, entries, state, destination, reviewed, review_sha256):
    from allin1_sdk.gxt2_desktop import _context, _digest, _path
    archive, game, binding = _source(state)
    # All mutation authority is restricted to the generated temporary directory.
    with tempfile.TemporaryDirectory(prefix=".allin1-rpf-package-", dir=destination.parent) as temporary:
        stage = Path(temporary).resolve()
        publication = stage / "package"
        output_archive = publication / "archive" / archive.name
        payload_root = publication / "payload"
        payload_root.mkdir(parents=True)
        service = RpfExplorerService(project_root(), game, workspace_roots=(stage,))
        service._require_game_closed()
        service._copy_verified(archive, output_archive, binding["outer_archive_sha256"])
        before_index = service.index(output_archive)
        entry = before_index.entry(binding["entry_id"])
        shape = _shape(before_index)
        if hashlib.sha256(json.dumps(shape, sort_keys=True).encode()).hexdigest() != reviewed["index_sha256"]:
            raise ValueError("Staged archive index differs from the reviewed source")
        selected = _verification_entries(before_index, entry)
        before = service.entry_content_fingerprints(before_index, selected)
        asset, _ = Gxt2Workspace.build(root, payload_root / "replacement.gxt2")
        if _hash(asset) != reviewed["payload_sha256"]:
            raise ValueError("Built dictionary differs from the reviewed payload")
        plan = service.replacement_plan(before_index, entry, asset)
        if plan["status"] != "ready" or plan["target_scope"] != "workspace_copy":
            raise ValueError("RPF package plan is not restricted to its staging copy")
        plan_file = stage / "replacement-plan.json"
        _write_json(plan_file, plan)
        receipt_file = service.apply_change_plan(plan_file, receipt_root=stage / "transactions")
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        if receipt["status"] != "applied":
            raise ValueError("Staged RPF transaction did not complete")
        after_index = service.index(output_archive)
        if _shape(after_index) != shape:
            raise ValueError("Packaged RPF changed unrelated archive structure")
        after = service.entry_content_fingerprints(after_index, _verification_entries(after_index, after_index.entry(entry.id)))
        if set(before) != set(after):
            raise ValueError("Packaged RPF payload inventory changed")
        comparisons = []
        for identity, original in before.items():
            actual = after[identity]
            if identity == entry.id:
                if actual["raw_sha256"] != reviewed["payload_sha256"]:
                    raise ValueError("Packaged RPF dictionary differs from the reviewed text")
            elif any(original[k] != actual[k] for k in ("mode", "logical_size", "canonical_sha256")):
                raise ValueError(f"Packaged RPF changed unrelated payload: {identity}")
            comparisons.append({"entry_id": identity, "changed": identity == entry.id,
                                "mode": actual["mode"], "before_sha256": original["canonical_sha256"],
                                "after_sha256": actual["canonical_sha256"]})
        rebuilt, _ = service.read_gxt2_entry(after_index, after_index.entry(entry.id))
        if Gxt2Workspace.parse(rebuilt) != Gxt2Workspace.parse(asset.read_bytes()):
            raise ValueError("Packaged dictionary failed semantic verification")
        if _hash(archive) != binding["outer_archive_sha256"]:
            raise ValueError("Original RPF changed during packaging; staged output discarded")
        _, _, final_state, _ = _context({"workspace": str(root)})
        if _digest(final_state) != _digest(state):
            raise ValueError("Text workspace changed during packaging; staged output discarded")
        archive_hash = _hash(output_archive)
        if receipt["applied_archive_sha256"] != archive_hash:
            raise ValueError("RPF receipt does not match the verified archive")
        report = {
            "schema_version": 1, "operation": "gxt2_rpf_package", "status": "verified",
            "source_binding": binding, "workspace": str(root), "workspace_state_sha256": _digest(state),
            "review_sha256": review_sha256, "review": reviewed,
            "archive": {"path": f"archive/{archive.name}", "size": output_archive.stat().st_size, "sha256": archive_hash},
            "replacement": {"entry_id": entry.id, "path": "payload/replacement.gxt2", "sha256": reviewed["payload_sha256"]},
            "transaction": {"status": receipt["status"], "plan_id": receipt["plan_id"],
                            "backup_verified_sha256": receipt["backup"]["sha256"], "scope": "temporary_workspace_copy"},
            "verification": comparisons, "source_unchanged": True, "game_write_performed": False,
            "installable_allin1_package": False,
        }
        report_file = publication / "rpf-package.json"
        _write_json(report_file, report)
        report_hash = _hash(report_file)
        service._require_game_closed()
        _path(str(destination), new=True, writable=True)
        # Windows directory rename refuses an existing destination. No source or
        # existing output is replaced. Temporary transaction backups are not shipped.
        if os.name != "nt":
            raise ValueError("RPF package publication currently requires Windows exclusive directory rename")
        publication.rename(destination)
    return {"kind": "gxt2_rpf_packaged", "destination": str(destination),
            "archive": str(destination / "archive" / archive.name), "sha256": archive_hash,
            "report": str(destination / "rpf-package.json"), "report_sha256": report_hash,
            "payload_sha256": reviewed["payload_sha256"], "verified_payloads": len(comparisons),
            "source_binding": binding, "review_sha256": review_sha256,
            "file_write_performed": True, "game_write_performed": False}
