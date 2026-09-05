"""Isolated compiled-render jobs and separately reviewed, exclusive PNG export.

Rendering writes only broker-owned cache artifacts, so cancellation can terminate
the whole read-only worker tree without interrupting a user-file transaction.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from PIL import Image

from allin1_sdk import compiled_render
from allin1_sdk.asset_preview import PreviewArtifactStore
from allin1_sdk.native_assets import NativeAssetInspector, MAX_NATIVE_PREVIEW_BYTES
from allin1_sdk.paths import project_root
from allin1_sdk.release_paths import no_links, strict_json
from allin1_sdk.workspace_desktop import path, file_hash, digest

_SHA = re.compile(r"[a-f0-9]{64}")
_MAX_RENDER_BYTES = 128 * 1024**2


def _blender(payload):
    explicit = payload.get("blender_executable")
    executable = path(explicit) if explicit else None
    if executable is not None and executable.name.casefold() not in {"blender", "blender.exe"}:
        raise ValueError("Choose the Blender executable")
    installation = compiled_render.detect_blender(executable)
    if installation is None:
        return None
    selected = no_links(installation.executable)
    return {"executable": str(selected), "version": installation.version,
            "source": installation.source, "sha256": file_hash(selected)}


def _input(value, suffixes):
    selected = path(value)
    if not selected.is_file() or selected.suffix.casefold() not in suffixes:
        raise ValueError("Choose a supported loose native asset")
    if not 0 < selected.stat().st_size <= MAX_NATIVE_PREVIEW_BYTES:
        raise ValueError("Native asset exceeds the bounded renderer input size")
    return selected, file_hash(selected)


def _context(payload):
    source, source_sha = _input(payload.get("source"), {".yft", ".ydr", ".ydd"})
    texture, texture_sha = _input(payload["texture_dictionary"], {".ytd"}) if payload.get("texture_dictionary") else (None, None)
    settings = payload.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("Render settings must be an object")
    configured = compiled_render.CompiledRenderSettings(**settings)
    camera = payload.get("camera", {})
    if not isinstance(camera, dict) or set(camera) - {"yaw", "pitch", "lod", "component"}:
        raise ValueError("Invalid render camera controls")
    camera = {"yaw": 34.0, "pitch": 18.0, "lod": None, "component": None, **camera}
    for key, low, high in (("yaw", -3600, 3600), ("pitch", -89, 89)):
        compiled_render._finite_between(camera[key], low, high, key)
    for key in ("lod", "component"):
        if camera[key] is not None and (not isinstance(camera[key], str) or len(camera[key]) > 512 or "\0" in camera[key]):
            raise ValueError("Render selections must be bounded strings")
    edition = payload.get("edition", "Enhanced")
    if edition not in {"Legacy", "Enhanced"}:
        raise ValueError("Choose Legacy or Enhanced for native decoding")
    game = path(payload["gta_path"]) if payload.get("gta_path") else None
    if game is not None and not game.is_dir():
        raise ValueError("Decoder context must select a folder")
    backend = project_root() / "tools" / "RpfPatcher"
    decoder = {name: file_hash(no_links(backend / name)) for name in ("RpfPatcher.exe", "RpfPatcher.dll", "CodeWalker.Core.dll") if (backend / name).is_file()}
    # Frozen modules live in the PYZ, not at module.__file__. Bind the exact
    # sidecar executable (which contains that renderer) in packaged builds.
    frozen = bool(getattr(sys, "frozen", False))
    renderer_file = Path(sys.executable) if frozen else Path(compiled_render.__file__)
    identity = {"source": str(source), "source_sha256": source_sha, "texture_dictionary": str(texture) if texture else None,
                "texture_sha256": texture_sha, "edition": edition, "gta_path": str(game) if game else None,
                "settings": asdict(configured), "camera": camera, "decoder_sha256": decoder,
                "renderer_schema": 1, "renderer_sha256": file_hash(renderer_file),
                "renderer_identity_kind": "frozen-sidecar" if frozen else "python-source", "python_version": sys.version}
    return source, texture, game, configured, identity


def _cache():
    configured = os.environ.get("ALLIN1_PREVIEW_DIR", "").strip()
    if not configured:
        raise ValueError("The desktop preview cache is unavailable")
    # Check every parent before creating even SDK-owned temporary artifacts.
    root = no_links(Path(configured))
    if not root.is_absolute():
        raise ValueError("The preview cache must be absolute")
    root.mkdir(parents=True, exist_ok=True)
    compiled = no_links(root / "compiled-renders")
    compiled.mkdir(exist_ok=True)
    # Fail closed instead of silently deleting a user's unsaved render.
    entries = list(compiled.iterdir())
    if len(entries) > 64 or sum(no_links(item).stat().st_size for item in entries if item.is_file()) > 512 * 1024**2:
        raise ValueError(f"Compiled-render cache is full; export retained renders before clearing {compiled}")
    return root, compiled


def inspect(payload):
    if type(payload.get("render", False)) is not bool:
        raise ValueError("Render selection must be a boolean")
    blender = _blender(payload)
    if not payload.get("render"):
        return {"state_sha256": digest(blender), "blender": blender, "render_ready": blender is not None}
    if blender is None:
        raise ValueError("Blender was not found; choose its executable or install Blender")
    source, texture, game, configured, identity = _context(payload)
    identity["blender"] = blender
    state = digest(identity)
    data = source.read_bytes()
    if hashlib.sha256(data).hexdigest() != identity["source_sha256"]:
        raise ValueError("Model changed before decoding")
    report = NativeAssetInspector(project_root(), game).inspect_bytes(source.name, data, edition=identity["edition"], truncated=False)
    if getattr(report, "metadata", {}).get("interpreted_edition", identity["edition"]) != identity["edition"]:
        raise ValueError("The model decoded as a different edition; select that edition explicitly")
    if report.model_scene is None:
        raise ValueError(next(iter(report.warnings), "Native model geometry could not be decoded"))
    root, cache = _cache()
    with tempfile.TemporaryDirectory(prefix="allin1-desktop-render-") as temporary:
        output = Path(temporary) / "frame.png"
        result = compiled_render.compile_vehicle_render(report.model_scene, output, settings=configured,
            blender_executable=blender["executable"], texture_dictionary=texture, edition=identity["edition"],
            gta_path=game, protected_roots=(source.parent,), **identity["camera"])
        # A long render must not publish evidence for inputs that changed mid-job.
        if _context(payload)[4] != {key: value for key, value in identity.items() if key != "blender"} or _blender(payload) != blender:
            raise ValueError("Render inputs or Blender changed while rendering; the result was discarded")
        if output.stat().st_size > _MAX_RENDER_BYTES:
            raise ValueError("Compiled PNG exceeds the bounded export size")
        image_bytes = output.read_bytes()
        image_sha = hashlib.sha256(image_bytes).hexdigest()
        record = {"schema_version": 1, "identity": identity, "state_sha256": state, "output_sha256": image_sha,
                  "size": len(image_bytes), "width": result.width, "height": result.height,
                  "elapsed_seconds": result.elapsed_seconds, "metadata": result.metadata,
                  "fidelity": result.metadata.get("fidelity", "Offline Blender rendering; not in-game Reactor acceptance")}
        # Normalize dataclass/tuple metadata before calculating its identity.
        record = json.loads(json.dumps(record, allow_nan=False))
        render_id = digest(record)
        destination = no_links(cache / f"{render_id}.png")
        manifest = no_links(cache / f"{render_id}.json")
        with destination.open("xb") as stream:
            stream.write(image_bytes)
        with manifest.open("x", encoding="utf-8") as stream:
            json.dump(record, stream, allow_nan=False)
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.thumbnail((1600, 1200))
            preview = io.BytesIO()
            image.save(preview, "PNG")
        artifact = PreviewArtifactStore(root).write_png(preview.getvalue())
    return {"state_sha256": state, "blender": blender, "render_ready": True, "render_id": render_id,
            "artifact": artifact, "render_record": record, "game_acceptance": "NOT TESTED"}


def _record(payload):
    render_id = payload.get("render_id")
    if not isinstance(render_id, str) or not _SHA.fullmatch(render_id):
        raise ValueError("Choose a completed render from this SDK session")
    _, cache = _cache()
    manifest = no_links(cache / f"{render_id}.json")
    if manifest.stat().st_size > 128 * 1024:
        raise ValueError("Render receipt exceeds its evidence limit")
    record = strict_json(manifest.read_bytes())
    if not isinstance(record, dict) or record.get("schema_version") != 1 or digest(record) != render_id:
        raise ValueError("Render receipt changed or is incompatible")
    source = no_links(cache / f"{render_id}.png")
    if not 0 < source.stat().st_size <= _MAX_RENDER_BYTES or file_hash(source) != record["output_sha256"]:
        raise ValueError("Rendered pixels changed; render again before export")
    if payload.get("expected_state_sha256") != record["state_sha256"]:
        raise ValueError("Render identity does not match the selected frame")
    return source, record


def review(payload):
    if payload.get("action") != "export":
        raise ValueError("Only reviewed PNG export is supported")
    source, record = _record(payload)
    destination = path(payload.get("destination"), new=True, writable=True)
    for key in ("source", "texture_dictionary"):
        original = record["identity"].get(key)
        if original and destination.is_relative_to(Path(original).parent):
            raise ValueError("Export renders outside the original model and texture folders")
    if destination.suffix.casefold() != ".png":
        raise ValueError("Render exports require a PNG filename")
    receipt = path(str(destination) + ".render.json", new=True, writable=True)
    return {"action": "export", "state_sha256": record["state_sha256"], "source": str(source), "destination": str(destination),
            "outputs": [str(destination), str(receipt)], "output_sha256": record["output_sha256"],
            "render_id": payload["render_id"], "fidelity": record["fidelity"]}


def apply(payload):
    source, record = _record(payload)
    destination = path(payload["destination"], new=True, writable=True)
    receipt = path(str(destination) + ".render.json", new=True, writable=True)
    content = source.read_bytes()
    if hashlib.sha256(content).hexdigest() != record["output_sha256"]:
        raise ValueError("Rendered pixels changed before export")
    created = []
    try:
        with destination.open("xb") as stream:
            created.append(destination)
            stream.write(content)
        with receipt.open("x", encoding="utf-8") as stream:
            created.append(receipt)
            json.dump(record, stream, indent=2, allow_nan=False)
    except BaseException:
        for item in reversed(created):
            item.unlink(missing_ok=True)
        raise
    return {"output": str(destination), "output_sha256": record["output_sha256"], "receipt": str(receipt)}
