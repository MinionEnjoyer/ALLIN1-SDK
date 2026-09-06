"""Reviewed YTD batches with private preparation and a recoverable grouped journal."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from allin1_sdk.release_paths import no_links, relative_path
from allin1_sdk.texture_workspace import (
    TextureDictionaryWorkspace, TextureRestoreResult, RASTER_TEXTURE_SUFFIXES,
    _sha256_file, _write_json_atomic, inspect_texture_source,
)
from allin1_sdk.texture_conversion import conversion_metadata

MAX_BATCH = 128
MAX_BYTES = 2 * 1024**3


def context(workspace, payload):
    catalog = workspace.catalog()
    if catalog.warnings:
        raise ValueError("Resolve texture dependency warnings before batch editing")
    records = {item.name.casefold(): item for item in catalog.textures}
    mode = payload.get("batch_action")
    operations = []
    if mode in {"convert", "remove"}:
        names = payload.get("texture_names")
        if not isinstance(names, list) or not 1 <= len(names) <= MAX_BATCH or any(not isinstance(name, str) for name in names):
            raise ValueError(f"Select 1–{MAX_BATCH} textures for a batch")
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("Batch texture selections must be distinct")
        for name in names:
            item = records.get(name.casefold())
            if item is None:
                raise ValueError(f"Unknown selected texture: {name}")
            operation = {"action": mode, "texture_name": item.name}
            if mode == "convert":
                mips = max(item.width, item.height).bit_length() if payload.get("mip_levels") == "full" else payload.get("mip_levels")
                metadata = conversion_metadata(item.width, item.height, payload.get("output_format"), mips)
                operation.update(output_format=payload["output_format"], mip_levels=metadata.mip_levels)
            operations.append(operation)
    elif mode == "import":
        raw = payload.get("source_folder")
        if not isinstance(raw, str) or not raw.strip() or "\0" in raw:
            raise ValueError("Choose an image folder for batch import")
        folder = no_links(Path(raw).expanduser()).resolve(strict=True)
        if not folder.is_dir() or folder.is_relative_to(workspace.root):
            raise ValueError("Batch image folder must be outside the texture workspace")
        policy = payload.get("import_policy")
        if policy not in {"replace", "add", "upsert"}:
            raise ValueError("Import policy must be replace, add, or upsert")
        images = []
        for path in folder.iterdir():
            if path.suffix.casefold() in RASTER_TEXTURE_SUFFIXES | {".dds"}:
                images.append(no_links(path))
                if len(images) > MAX_BATCH:
                    raise ValueError(f"Batch image folder exceeds {MAX_BATCH} images; split it into batches")
        for path in sorted(images, key=lambda p: p.name.casefold()):
            name = workspace.validate_texture_name(path.stem)
            existing = records.get(name.casefold())
            if policy == "replace" and not existing or policy == "add" and existing:
                continue
            source = inspect_texture_source(path).to_dict()
            operations.append({"action": "replace" if existing else "add", "texture_name": existing.name if existing else name,
                               "source_image": source["source"], "source_sha256": source["sha256"], "source_size": source["size"],
                               "source_width": source["width"], "source_height": source["height"], "source_format": source["format"], "source_mips": source["mip_levels"]})
        if not operations:
            raise ValueError("No images match the selected import policy (flat folder; filename stem = texture name)")
        if len({item["texture_name"].casefold() for item in operations}) != len(operations):
            raise ValueError("Multiple source images map to the same texture name")
    else:
        raise ValueError("Batch action must be convert, remove, or import")
    input_bytes = sum(item.size or 0 for item in catalog.textures) + sum(item.get("source_size", 0) for item in operations)
    if input_bytes > MAX_BYTES:
        raise ValueError("Texture batch exceeds the 2 GiB staging input limit")
    # Reserve a conservative RGBA full-chain allowance even for compressed outputs.
    output_bound = sum(item.width * item.height * 6 + 148 for item in catalog.textures)
    if mode == "import":
        output_bound += sum(item["source_width"] * item["source_height"] * 4 + 148 for item in operations)
    if output_bound > MAX_BYTES:
        raise ValueError("Texture batch exceeds the 2 GiB decoded-output safety bound; reduce texture sizes")
    review = {
        "kind": "texture_edit_review", "operation": "review_texture_edit", "action": "batch", "batch_action": mode,
        "workspace": str(workspace.root), "state_sha256": workspace.state_sha256(), "revision": workspace.revision,
        "texture_name": f"{len(operations)} textures", "source": None, "operations": operations,
        "changes": [{"field": item["texture_name"], "before": (
            f"{records[item['texture_name'].casefold()].format} / {records[item['texture_name'].casefold()].width}×{records[item['texture_name'].casefold()].height} / {records[item['texture_name'].casefold()].mip_levels} mips"
            if item["texture_name"].casefold() in records else "(absent)"),
            "after": f"{item['output_format']} / {item['mip_levels']} mips" if item["action"] == "convert" else
            f"{item['source_image']} / {item['source_width']}×{item['source_height']} / {item['source_format']} / {item['source_mips']} mips" if "source_image" in item else "(removed)"} for item in operations],
        "warning": "One batch undo point. Removal can break external bindings. Raster imports produce one RGBA mip. Conversion regenerates box-filtered mips; compression is lossy, DXT1 drops alpha, and normal vectors/color space are not reconstructed.",
        "ready": True, "review_only": True, "workspace_write_performed": False, "package_write_performed": False, "game_write_performed": False,
    }
    review["review_sha256"] = hashlib.sha256(json.dumps(review, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"action": "batch", "operations": operations}, review


def _managed(workspace, relative):
    path = no_links(workspace.root / relative_path(relative)).resolve()
    if path != workspace.xml and (not path.is_relative_to(workspace.assets) or path.suffix.casefold() != ".dds"):
        raise ValueError("Batch journal contains a path outside managed XML/DDS dependencies")
    return path


def _hash(path):
    no_links(path)
    if path.exists() and not path.is_file():
        raise ValueError(f"Texture batch target is not a file: {path}")
    return _sha256_file(path) if path.is_file() else None


def _install(workspace, sources, expected):
    # XML last keeps the old catalog readable until the dependency commit finishes.
    for relative in sorted(sources, key=lambda r: _managed(workspace, r) == workspace.xml):
        target = _managed(workspace, relative)
        if _hash(target) != expected[relative]:
            raise ValueError(f"Texture batch target changed during commit: {relative}")
        source = sources[relative]
        if source is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            staged = target.with_name(f".{target.name}.{uuid4().hex}.batch")
            try:
                shutil.copyfile(no_links(source), staged)
                if _hash(target) != expected[relative]:
                    raise ValueError(f"Texture batch target changed during commit: {relative}")
                staged.replace(target)
            finally:
                staged.unlink(missing_ok=True)


def _commit(workspace, sources, action="batch"):
    before = {relative: _hash(_managed(workspace, relative)) for relative in sources}
    after = {relative: _hash(source) if source else None for relative, source in sources.items()}
    history_root = no_links(workspace.root / "history")
    history_root.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    history = history_root / f"{stamp}-{action}-{uuid4().hex}"
    history.mkdir()
    backups = {}
    for relative, digest in before.items():
        backup = history / "before" / relative
        if digest is not None:
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(_managed(workspace, relative), backup)
            if _hash(backup) != digest:
                raise ValueError("Texture changed while preparing batch recovery")
        backups[relative] = backup if digest is not None else None
    record = {"schema_version": 1, "operation": "ytd_texture_batch", "phase": "committing", "before": before, "after": after}
    _write_json_atomic(history / "edit.json", record)
    try:
        _install(workspace, sources, before)
        workspace.catalog()
        record["phase"] = "committed"
        _write_json_atomic(history / "edit.json", record)
    except Exception as error:
        try:
            current = {r: _hash(_managed(workspace, r)) for r in sources}
            if any(current[r] not in {before[r], after[r]} for r in sources):
                raise ValueError("Concurrent edit detected; refusing to overwrite it during recovery")
            _install(workspace, backups, current)
            history.rename(history.with_name(history.name + ".restored"))
        except Exception as recovery:
            raise RuntimeError(f"Batch failed; recovery retained at {history}: {recovery}") from error
        raise
    return history


def apply(workspace, operations):
    initial = workspace.state_sha256()
    original_files = {workspace.xml.relative_to(workspace.root).as_posix(): workspace.xml}
    original_files.update({workspace.texture_path(item.name).relative_to(workspace.root).as_posix(): workspace.texture_path(item.name) for item in workspace.catalog().textures})
    output_bound = sum(item.width * item.height * 6 + 148 for item in workspace.catalog().textures)
    output_bound += sum(item.get("source_width", 0) * item.get("source_height", 0) * 4 + 148 for item in operations)
    required_space = 3 * sum(path.stat().st_size for path in original_files.values()) + output_bound + sum(item.get("source_size", 0) for item in operations) + 64 * 1024**2
    if shutil.disk_usage(workspace.root).free < required_space:
        raise ValueError("Insufficient free space for staged texture batch and recovery snapshots")
    with tempfile.TemporaryDirectory(prefix=".allin1-texture-batch-", dir=workspace.root) as temporary:
        staged_root = Path(temporary) / "workspace"
        staged_root.mkdir()
        shutil.copyfile(workspace.root / "native-workspace.json", staged_root / "native-workspace.json")
        for relative, source in original_files.items():
            destination = staged_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        staged = TextureDictionaryWorkspace(staged_root)
        for operation in operations:
            name, action = operation["texture_name"], operation["action"]
            if action in {"add", "replace"}:
                source = no_links(Path(operation["source_image"]))
                image = Path(temporary) / f"input-{uuid4().hex}{source.suffix}"
                shutil.copyfile(source, image)
                if _hash(image) != operation["source_sha256"]:
                    raise ValueError("Batch import image changed after review")
                getattr(staged, action)(name, image)
            elif action == "convert":
                staged.convert(name, operation["output_format"], operation["mip_levels"])
            else:
                staged.remove(name)
        if staged.catalog().warnings:
            raise ValueError("Prepared texture batch has dependency warnings")
        final_files = {staged.xml.relative_to(staged.root).as_posix(): staged.xml}
        final_files.update({staged.texture_path(item.name).relative_to(staged.root).as_posix(): staged.texture_path(item.name) for item in staged.catalog().textures})
        if sum(path.stat().st_size for path in final_files.values()) > MAX_BYTES:
            raise ValueError("Prepared texture batch exceeds 2 GiB")
        if workspace.state_sha256() != initial:
            raise ValueError("Texture workspace changed while preparing batch")
        for relative in final_files.keys() - original_files.keys():
            if _managed(workspace, relative).exists():
                raise ValueError("New texture dependency would replace an unrelated existing file")
        changed = {}
        for relative in original_files.keys() | final_files.keys():
            before = _hash(original_files[relative]) if relative in original_files else None
            after = _hash(final_files[relative]) if relative in final_files else None
            if before != after:
                changed[relative] = final_files.get(relative)
        if not changed:
            raise ValueError("Texture batch makes no changes")
        if workspace.state_sha256() != initial:
            raise ValueError("Texture workspace changed before batch commit")
        return _commit(workspace, changed)


def restore(workspace, selected, record):
    before, after = record.get("before"), record.get("after")
    if record.get("schema_version") != 1 or record.get("phase") not in {"committing", "committed"} or not isinstance(before, dict) or not isinstance(after, dict) or before.keys() != after.keys() or not 1 <= len(before) <= 4097:
        raise ValueError("Invalid texture batch recovery journal")
    sources = {}
    for relative, digest in before.items():
        current = _hash(_managed(workspace, relative))
        allowed = {after[relative]} if record.get("phase") == "committed" else {digest, after[relative]}
        if current not in allowed:
            raise ValueError("Texture changed since batch commit; refusing to overwrite it during undo")
        backup = no_links(selected / "before" / relative_path(relative))
        if digest is not None and _hash(backup) != digest:
            raise ValueError("Texture batch backup is missing or changed")
        sources[relative] = backup if digest is not None else None
    recovery = _commit(workspace, sources, "restore-backup")
    restored = selected.with_name(selected.name + ".restored")
    selected.rename(restored)
    return TextureRestoreResult(restored, recovery, workspace.catalog())
