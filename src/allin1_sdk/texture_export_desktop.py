"""Reviewed bulk texture exports; no workspace or game mutation."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from PIL import Image

from allin1_sdk.paths import gta_root_containing
from allin1_sdk.release_paths import no_links, relative_path, unique_paths
from allin1_sdk.texture_workspace import MAX_YTD_TEXTURES, _sha256_file

MAX_EXPORT_BYTES = 2 * 1024**3
MAX_EXPORT_PIXELS = 512 * 1024**2


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _context(payload, risk):
    from allin1_sdk.desktop_protocol import _texture_workspace, _texture_workspace_snapshot
    if not isinstance(payload, dict):
        raise ValueError("Texture export payload must be an object")
    raw = payload.get("workspace")
    if not isinstance(raw, str):
        raise ValueError("Texture export requires a workspace")
    no_links(Path(raw).expanduser())
    workspace = _texture_workspace(payload, risk=risk)
    _texture_workspace_snapshot(workspace)  # Validate immutable source identity too.
    state = workspace.state_sha256()
    if payload.get("expected_state_sha256") != state:
        raise ValueError("Texture workspace changed after it was loaded")
    format_name = payload.get("format")
    if format_name not in {"dds", "png"}:
        raise ValueError("Texture export format must be dds or png")
    textures = workspace.catalog().textures
    mode = payload.get("mode")
    names = payload.get("texture_names")
    if mode == "all":
        if names not in (None, []):
            raise ValueError("All-texture export cannot also specify a selection")
        selected = list(textures)
    elif mode == "selected":
        if not isinstance(names, list) or not 1 <= len(names) <= MAX_YTD_TEXTURES or any(not isinstance(n, str) for n in names) or len(set(names)) != len(names):
            raise ValueError("Select unique texture names for export")
        known = {t.name: t for t in textures}
        if any(n not in known for n in names):
            raise ValueError("Selected texture was not found")
        selected = [known[n] for n in names]
    else:
        raise ValueError("Texture export mode must be selected or all")
    if not selected:
        raise ValueError("No textures to export")
    selected.sort(key=lambda t: (t.name.casefold(), t.name))
    if any(t.size is None or t.sha256 is None or t.warnings for t in selected):
        raise ValueError("Resolve selected texture dependency warnings before export")
    total_size = sum(t.size for t in selected)
    pixels = sum(t.width * t.height for t in selected)
    if total_size > MAX_EXPORT_BYTES or (format_name == "png" and pixels > MAX_EXPORT_PIXELS):
        raise ValueError("Texture export exceeds the 2 GiB source / 512 megapixel PNG batch limit; select a smaller batch")
    raw_destination = payload.get("destination")
    if not isinstance(raw_destination, str) or not raw_destination.strip() or len(raw_destination) > 4096:
        raise ValueError("Texture export requires a new destination folder")
    authored = no_links(Path(raw_destination).expanduser())
    relative_path(authored.name)
    parent = authored.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError("Export parent must be a directory")
    destination = parent / authored.name
    if destination.exists() or destination.is_relative_to(workspace.root):
        raise ValueError("Export destination must be new and outside the workspace")
    raw_gta = payload.get("gta_path")
    roots = ()
    if raw_gta not in (None, ""):
        if not isinstance(raw_gta, str):
            raise ValueError("Invalid game path")
        root = no_links(Path(raw_gta).expanduser()).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Game path must be a directory")
        roots = (root,)
    if gta_root_containing(destination, explicit_roots=roots):
        raise ValueError("Texture exports must be outside GTA V")
    entries = []
    for texture in selected:
        source = no_links(workspace.texture_path(texture.name))
        if _sha256_file(source) != texture.sha256:
            raise ValueError("Texture dependency changed during export review")
        name = f"{texture.name}.{format_name}"
        entries.append({"texture": texture.name, "file": name, "source_sha256": texture.sha256, "source_size": texture.size})
    unique_paths([e["file"] for e in entries] + ["allin1-texture-export.json"])
    review = {
        "kind": "texture_export_review", "workspace": str(workspace.root),
        "state_sha256": state, "destination": str(destination), "mode": mode,
        "format": format_name, "texture_count": len(entries), "source_bytes": total_size,
        "entries_sha256": _digest(entries), "entries_preview": entries[:100],
        "preview_truncated": len(entries) > 100,
        "warning": "PNG exports only the top mip as RGBA; DDS preserves original bytes and every mip." if format_name == "png" else "DDS files are copied byte-for-byte, including all mip levels.",
        "ready": True, "review_only": True, "workspace_write_performed": False,
        "game_write_performed": False, "output_write_performed": False,
    }
    review["review_sha256"] = _digest(review)
    return workspace, destination, entries, review


def run(operation, payload):
    from allin1_sdk.desktop_protocol import ProtocolError
    applying = operation == "apply_texture_export"
    risk = "authoring_write" if applying else "read_only"
    try:
        if applying and (not isinstance(payload, dict) or payload.get("authoring_confirmed") is not True):
            raise ValueError("Texture export requires action-time confirmation")
        workspace, destination, entries, review = _context(payload, risk)
        if not applying:
            return risk, review
        if payload.get("review_sha256") != review["review_sha256"]:
            raise ValueError("Texture export changed after review")
        with tempfile.TemporaryDirectory(prefix=".allin1-textures-", dir=destination.parent) as temporary:
            staged = Path(temporary) / "export"
            staged.mkdir()
            outputs = []
            output_size = 0
            for entry in entries:
                source = no_links(workspace.texture_path(entry["texture"]))
                target = staged / entry["file"]
                if review["format"] == "dds":
                    shutil.copyfile(source, target)
                    if _sha256_file(target) != entry["source_sha256"]:
                        raise ValueError("DDS dependency changed during export")
                else:
                    # Inspect/decode only the already bounded source dimensions.
                    with Image.open(source) as image:
                        if image.width * image.height > MAX_EXPORT_PIXELS:
                            raise ValueError("Image changed beyond the PNG export limit")
                        image.convert("RGBA").save(target, format="PNG")
                    if _sha256_file(source) != entry["source_sha256"]:
                        raise ValueError("DDS dependency changed during PNG conversion")
                    with Image.open(target) as verify:
                        verify.verify()
                size = target.stat().st_size
                output_size += size
                if output_size > MAX_EXPORT_BYTES:
                    raise ValueError("Export output exceeds 2 GiB; select a smaller batch")
                outputs.append({**entry, "size": size, "sha256": _sha256_file(target)})
            if workspace.state_sha256() != review["state_sha256"]:
                raise ValueError("Texture workspace changed during export")
            receipt = {"schema_version": 1, "operation": operation, "workspace": str(workspace.root),
                       "state_sha256": review["state_sha256"], "review_sha256": review["review_sha256"],
                       "format": review["format"], "outputs": outputs}
            receipt_path = staged / "allin1-texture-export.json"
            receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
            receipt_sha = _sha256_file(receipt_path)
            no_links(destination)
            if destination.exists():
                raise ValueError("Export destination appeared during conversion; nothing was overwritten")
            # Windows directory rename fails if the destination already exists.
            staged.rename(destination)
        return risk, {"kind": "texture_export_result", "destination": str(destination),
                      "texture_count": len(outputs), "output_bytes": output_size,
                      "receipt": str(destination / "allin1-texture-export.json"), "receipt_sha256": receipt_sha,
                      "workspace_write_performed": False, "game_write_performed": False,
                      "output_write_performed": True}
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ProtocolError(str(exc), risk=risk) from exc
