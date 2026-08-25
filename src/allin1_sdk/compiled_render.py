"""Optional, isolated Blender rendering for decoded vehicle model scenes.

The interactive SDK viewport intentionally stays lightweight.  This module
provides an opt-in offline render path that exports a decoded
``NativeModelScene`` into a temporary OBJ/MTL interchange, builds a controlled
Blender scene, and copies only the verified PNG result to the requested output
path.  Blender is discovered at runtime and is never a package dependency.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import warnings
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from allin1_sdk.native_assets import (
    MAX_MODEL_TRIANGLES,
    NativeAssetInspector,
    NativeModelScene,
    _model_geometry_bounds,
    _model_material_identity,
)
from allin1_sdk.processes import hidden_process_options
from allin1_sdk.texture_workspace import TextureDictionaryWorkspace


BLENDER_ENGINES = frozenset({"eevee", "cycles"})
BLENDER_DEVICES = frozenset({"auto", "cpu", "gpu"})
COMPILED_RENDER_QUALITIES = frozenset({"preview", "production", "maximum"})
COMPILED_LIGHT_RIGS = frozenset({"studio", "outdoor", "dramatic", "neutral"})
COMPILED_BACKGROUNDS = frozenset({
    "studio_dark", "studio_light", "transparent", "custom",
})
MAX_COMPILED_RESOLUTION = 15360
MAX_COMPILED_PIXELS = 15360 * 8640
MAX_COMPILED_SAMPLES = 4096
_BLENDER_VERSION = re.compile(r"Blender\s+(?P<version>\d+(?:\.\d+){1,3})", re.I)
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class CompiledRenderSettings:
    """Validated controls for one offline render.

    ``samples=None`` selects an engine- and quality-appropriate default.
    Lighting strengths are a multiplier over a deliberately conservative rig.
    """

    width: int = 1920
    height: int = 1080
    quality: str = "production"
    samples: int | None = None
    engine: str = "eevee"
    device: str = "auto"
    light_rig: str = "studio"
    light_rotation_deg: float = 0.0
    light_strength: float = 1.0
    background: str = "studio_dark"
    background_color: str = "#111714"
    transparent: bool = False
    lens_mm: float = 52.0
    ground_plane: bool = True
    contact_shadows: bool = True

    def __post_init__(self) -> None:
        if isinstance(self.width, bool) or not isinstance(self.width, int):
            raise ValueError("Compiled render width must be an integer")
        if isinstance(self.height, bool) or not isinstance(self.height, int):
            raise ValueError("Compiled render height must be an integer")
        if not 256 <= self.width <= MAX_COMPILED_RESOLUTION:
            raise ValueError(
                f"Compiled render width must be between 256 and {MAX_COMPILED_RESOLUTION}"
            )
        if not 256 <= self.height <= MAX_COMPILED_RESOLUTION:
            raise ValueError(
                f"Compiled render height must be between 256 and {MAX_COMPILED_RESOLUTION}"
            )
        if self.width * self.height > MAX_COMPILED_PIXELS:
            raise ValueError(
                "Compiled render area may not exceed 16K UHD "
                f"({MAX_COMPILED_PIXELS:,} pixels)"
            )
        normalized_quality = _choice(self.quality, COMPILED_RENDER_QUALITIES, "quality")
        normalized_engine = _choice(self.engine, BLENDER_ENGINES, "engine")
        normalized_device = _choice(self.device, BLENDER_DEVICES, "device")
        normalized_rig = _choice(self.light_rig, COMPILED_LIGHT_RIGS, "light rig")
        background_value = (
            "custom"
            if isinstance(self.background, str)
            and self.background.strip().casefold() == "custom_color"
            else self.background
        )
        normalized_background = _choice(
            background_value, COMPILED_BACKGROUNDS, "background",
        )
        object.__setattr__(self, "quality", normalized_quality)
        object.__setattr__(self, "engine", normalized_engine)
        object.__setattr__(self, "device", normalized_device)
        object.__setattr__(self, "light_rig", normalized_rig)
        object.__setattr__(self, "background", normalized_background)
        if self.samples is not None and (
            isinstance(self.samples, bool)
            or not isinstance(self.samples, int)
            or not 1 <= self.samples <= MAX_COMPILED_SAMPLES
        ):
            raise ValueError(
                f"Compiled render samples must be between 1 and {MAX_COMPILED_SAMPLES}"
            )
        _finite_between(
            self.light_rotation_deg, -3600.0, 3600.0, "light rotation",
        )
        _finite_between(self.light_strength, 0.05, 10.0, "light strength")
        _finite_between(self.lens_mm, 18.0, 200.0, "camera lens")
        if not isinstance(self.background_color, str) or not _HEX_COLOR.fullmatch(
            self.background_color.strip()
        ):
            raise ValueError("Compiled render background color must use #RRGGBB")
        object.__setattr__(self, "background_color", self.background_color.upper())
        if not isinstance(self.transparent, bool):
            raise ValueError("Compiled render transparent setting must be true or false")
        if not isinstance(self.ground_plane, bool):
            raise ValueError("Compiled render ground-plane setting must be true or false")
        if not isinstance(self.contact_shadows, bool):
            raise ValueError("Compiled render contact-shadow setting must be true or false")

    @property
    def effective_samples(self) -> int:
        if self.samples is not None:
            return self.samples
        defaults = {
            "eevee": {"preview": 32, "production": 128, "maximum": 256},
            "cycles": {"preview": 32, "production": 256, "maximum": 512},
        }
        return defaults[self.engine][self.quality]


@dataclass(frozen=True)
class BlenderInstallation:
    executable: Path
    version: str
    source: str


@dataclass(frozen=True)
class CompiledRenderProgress:
    stage: str
    fraction: float
    message: str


@dataclass(frozen=True)
class RenderInterchange:
    directory: Path
    obj_path: Path
    mtl_path: Path
    manifest_path: Path
    vertex_count: int
    triangle_count: int
    geometry_count: int
    material_count: int
    texture_count: int
    textured_material_count: int
    unresolved_texture_names: tuple[str, ...]
    sha256: str


@dataclass(frozen=True)
class _TextureDictionaryExport:
    source: Path
    assets: Mapping[str, Path]
    dictionary_texture_count: int
    exported_texture_count: int
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompiledRenderResult:
    output_path: Path
    width: int
    height: int
    elapsed_seconds: float
    metadata: dict[str, Any] = field(default_factory=dict)


class CompiledRenderError(RuntimeError):
    """A structured failure suitable for desktop, CLI, and agent surfaces."""

    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


class BlenderProcessRunner(Protocol):
    def __call__(
        self, command: Sequence[str], *, cwd: Path, timeout: float,
        cancel_event: threading.Event | None,
    ) -> subprocess.CompletedProcess[str]: ...


ProgressCallback = Callable[[CompiledRenderProgress], None]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _choice(value: Any, choices: frozenset[str], label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Compiled render {label} must be a string")
    normalized = value.strip().casefold()
    if normalized not in choices:
        raise ValueError(
            f"Compiled render {label} must be one of: {', '.join(sorted(choices))}"
        )
    return normalized


def _finite_between(value: Any, minimum: float, maximum: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Compiled render {label} must be a number")
    converted = float(value)
    if not math.isfinite(converted) or not minimum <= converted <= maximum:
        raise ValueError(
            f"Compiled render {label} must be between {minimum:g} and {maximum:g}"
        )
    return converted


def _default_process_runner(
    command: Sequence[str], *, cwd: Path, timeout: float,
    cancel_event: threading.Event | None,
) -> subprocess.CompletedProcess[str]:
    options: dict[str, Any] = {
        "cwd": str(cwd),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "shell": False,
    }
    options.update(hidden_process_options())
    started = time.monotonic()
    process = subprocess.Popen([str(value) for value in command], **options)
    captured: list[str] = ["", ""]
    communication_error: list[BaseException] = []

    def communicate() -> None:
        try:
            stdout, stderr = process.communicate()
            captured[0] = stdout or ""
            captured[1] = stderr or ""
        except BaseException as exc:  # surfaced on the calling thread below
            communication_error.append(exc)

    # Blender can emit enough progress text to fill an undrained pipe during a
    # high-resolution render.  Drain both streams while retaining the polling
    # loop used for cancellation and the hard deadline.
    communicator = threading.Thread(
        target=communicate, name="allin1-blender-output", daemon=True,
    )
    communicator.start()
    try:
        while communicator.is_alive():
            if cancel_event is not None and cancel_event.is_set():
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10.0)
                communicator.join(timeout=10.0)
                raise CompiledRenderError(
                    "render_cancelled", "The compiled render was cancelled",
                )
            if time.monotonic() - started > timeout:
                process.kill()
                try:
                    process.wait(timeout=10.0)
                except subprocess.TimeoutExpired:
                    pass
                communicator.join(timeout=10.0)
                raise CompiledRenderError(
                    "render_timeout",
                    f"Blender did not finish within {timeout:.0f} seconds",
                )
            time.sleep(0.05)
        communicator.join()
        if communication_error:
            raise communication_error[0]
        return subprocess.CompletedProcess(
            [str(value) for value in command], process.returncode,
            stdout=captured[0], stderr=captured[1],
        )
    finally:
        if process.poll() is None:
            process.kill()
            try:
                process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                pass
        communicator.join(timeout=10.0)


def _candidate_blender_paths(explicit: str | Path | None) -> list[tuple[Path, str]]:
    if explicit is not None:
        return [(Path(explicit).expanduser(), "explicit")]
    candidates: list[tuple[Path, str]] = []
    configured = os.environ.get("BLENDER_EXECUTABLE", "").strip()
    if configured:
        candidates.append((Path(configured).expanduser(), "environment"))
    discovered = shutil.which("blender")
    if discovered:
        candidates.append((Path(discovered), "PATH"))
    if os.name == "nt":
        roots = [
            os.environ.get("ProgramFiles", ""),
            os.environ.get("ProgramW6432", ""),
        ]
        for raw_root in roots:
            if not raw_root:
                continue
            foundation = Path(raw_root) / "Blender Foundation"
            try:
                paths = sorted(
                    foundation.glob("Blender */blender.exe"), reverse=True,
                )
            except OSError:
                paths = []
            candidates.extend((path, "standard install") for path in paths)
    unique: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for path, source in candidates:
        try:
            key = str(path.resolve(strict=False)).casefold()
        except OSError:
            key = str(path.absolute()).casefold()
        if key not in seen:
            seen.add(key)
            unique.append((path, source))
    return unique


def detect_blender(
    executable: str | Path | None = None, *,
    process_runner: BlenderProcessRunner | None = None,
) -> BlenderInstallation | None:
    """Locate and validate Blender without introducing a hard dependency."""

    runner = process_runner or _default_process_runner
    for candidate, source in _candidate_blender_paths(executable):
        try:
            resolved = candidate.resolve(strict=True)
            if not resolved.is_file():
                continue
            completed = runner(
                [str(resolved), "--version"], cwd=resolved.parent,
                timeout=15.0, cancel_event=None,
            )
        except (OSError, CompiledRenderError, subprocess.SubprocessError):
            continue
        if completed.returncode != 0:
            continue
        combined = f"{completed.stdout or ''}\n{completed.stderr or ''}"
        match = _BLENDER_VERSION.search(combined)
        if match:
            return BlenderInstallation(
                executable=resolved, version=match.group("version"), source=source,
            )
    return None


def _selected_geometries(
    scene: NativeModelScene, *, lod: str | None, component: str | None,
) -> tuple[Any, ...]:
    selected = scene.geometries
    if lod and lod.casefold() != "all":
        selected = tuple(item for item in selected if item.lod.casefold() == lod.casefold())
        if not selected:
            raise CompiledRenderError("lod_not_found", f"Model LOD was not found: {lod}")
    if component and component.casefold() != "all":
        selected = tuple(
            item for item in selected
            if item.component.casefold() == component.casefold()
        )
        if not selected:
            raise CompiledRenderError(
                "component_not_found",
                f"Model component was not found in the selected LOD: {component}",
            )
    if not selected:
        raise CompiledRenderError("empty_scene", "The decoded model scene has no geometry")
    return selected


def _material_properties(geometry: Any) -> tuple[str, str, tuple[int, int, int], dict[str, float]]:
    identity, semantic, color = _model_material_identity(geometry)
    properties = {"metallic": 0.0, "roughness": 0.42, "alpha": 1.0, "emission": 0.0}
    if semantic == "glass":
        properties.update(metallic=0.05, roughness=0.08, alpha=0.26)
    elif semantic in {"chrome", "metal", "wheel"}:
        properties.update(metallic=0.82, roughness=0.18)
    elif semantic == "paint":
        properties.update(metallic=0.32, roughness=0.2)
    elif semantic == "tyre":
        properties.update(roughness=0.78)
    elif semantic in {"interior", "plastic"}:
        properties.update(roughness=0.58)
    elif semantic == "light":
        properties.update(metallic=0.05, roughness=0.16, emission=1.2)
    return identity, semantic, color, properties


def _texture_role(slot: str, texture_name: str) -> str:
    """Classify an authored shader sampler without guessing unsupported channels."""
    folded_slot = slot.casefold().replace("_", "")
    folded_name = texture_name.casefold()
    if "normal" in folded_slot or "bump" in folded_slot:
        return "normal"
    if "spec" in folded_slot or "rough" in folded_slot:
        return "specular"
    if "emiss" in folded_slot:
        return "emissive"
    if "diffuse" in folded_slot or "albedo" in folded_slot or "basecolor" in folded_slot:
        return "diffuse"
    if any(token in folded_slot for token in ("damage", "dirt", "detail", "mask")):
        return "auxiliary"
    stem = Path(folded_name).stem
    if stem.endswith(("_n", "_nm", "_normal")):
        return "normal"
    if stem.endswith(("_s", "_spec", "_specular")):
        return "specular"
    if stem.endswith(("_e", "_em", "_emissive")):
        return "emissive"
    if stem.endswith(("_d", "_bc", "_a", "_diff", "_diffuse")):
        return "diffuse"
    return "unclassified"


def _export_texture_dictionary(
    texture_dictionary: str | Path, directory: Path, *,
    referenced_names: Iterable[str], edition: str,
    gta_path: str | Path | None,
) -> _TextureDictionaryExport:
    """Decode one YTD into isolated, Blender-readable texture dependencies."""
    authored = Path(texture_dictionary).expanduser()
    if authored.is_symlink():
        raise CompiledRenderError(
            "unsafe_texture_dictionary", "Texture dictionary cannot be a symbolic link",
        )
    try:
        source = authored.resolve(strict=True)
    except OSError as exc:
        raise CompiledRenderError(
            "texture_dictionary_not_found", "Texture dictionary was not found",
            {"path": str(authored)},
        ) from exc
    if not source.is_file() or source.suffix.casefold() != ".ytd":
        raise CompiledRenderError(
            "invalid_texture_dictionary", "Compiled render textures require one YTD file",
            {"path": str(source)},
        )
    project_root = Path(__file__).resolve().parents[2]
    native_workspace = directory / "texture-dictionary-workspace"
    try:
        NativeAssetInspector(project_root, gta_path).export_workspace(
            source, native_workspace, edition=edition,
        )
        catalog = TextureDictionaryWorkspace(native_workspace).catalog()
    except (OSError, RuntimeError, ValueError) as exc:
        raise CompiledRenderError(
            "texture_dictionary_decode_failed",
            "The linked YTD could not be decoded into a verified texture workspace",
            {"path": str(source), "error": str(exc)},
        ) from exc

    requested = {value.casefold() for value in referenced_names if value}
    records = {
        item.name.casefold(): item for item in catalog.textures
        if item.name.casefold() in requested and not item.warnings
    }
    output = directory / "textures"
    output.mkdir()
    exported: dict[str, Path] = {}
    warnings: list[str] = list(catalog.warnings)
    for index, key in enumerate(sorted(records)):
        record = records[key]
        source_image = catalog.assets / record.file_name
        if not source_image.is_file() or source_image.is_symlink():
            warnings.append(f"{record.name}: texture dependency is missing or unsafe")
            continue
        safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", record.name).strip("._") or "texture"
        destination = output / f"{index:04d}-{safe_stem}.png"
        try:
            with Image.open(source_image) as opened:
                opened.load()
                opened.convert("RGBA").save(destination, format="PNG", optimize=False)
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            warnings.append(f"{record.name}: DDS conversion failed ({exc})")
            continue
        exported[key] = destination
    return _TextureDictionaryExport(
        source=source, assets=exported,
        dictionary_texture_count=len(catalog.textures),
        exported_texture_count=len(exported), warnings=tuple(warnings),
    )


def export_render_interchange(
    scene: NativeModelScene, directory: str | Path, *,
    lod: str | None = None, component: str | None = None,
    texture_assets: Mapping[str, Path] | None = None,
) -> RenderInterchange:
    """Export bounded decoded geometry to an isolated OBJ/MTL scene."""

    target = Path(directory).resolve(strict=True)
    if not target.is_dir():
        raise CompiledRenderError("invalid_workspace", "Render workspace is not a directory")
    selected = _selected_geometries(scene, lod=lod, component=component)
    minima, maxima, vertex_count = _model_geometry_bounds(list(selected))
    triangle_count = sum(len(item.triangles) for item in selected)
    if vertex_count > 1_000_000 or triangle_count > MAX_MODEL_TRIANGLES:
        raise CompiledRenderError(
            "scene_limit",
            "The decoded model exceeds the guarded compiled-render geometry limit",
            {"vertices": vertex_count, "triangles": triangle_count},
        )

    # Texture pixels are optional render companions.  A stale catalog entry,
    # zero-byte conversion, or removed file must leave that sampler unresolved
    # rather than aborting an otherwise valid geometry/material preview.
    resolved_assets: dict[str, Path] = {}
    for key, value in (texture_assets or {}).items():
        if not isinstance(key, str) or not key.strip():
            continue
        try:
            authored = Path(value)
            if authored.is_symlink():
                continue
            resolved = authored.resolve(strict=True)
            if not resolved.is_file() or resolved.stat().st_size <= 0:
                continue
        except (OSError, TypeError, ValueError):
            continue
        resolved_assets[key.casefold()] = resolved
    material_keys: dict[tuple[Any, ...], str] = {}
    material_records: list[dict[str, Any]] = []
    geometry_materials: list[str] = []
    for geometry in selected:
        identity, semantic, color, properties = _material_properties(geometry)
        raw_parameters = tuple(getattr(geometry, "texture_parameters", ())) or tuple(
            ("", value) for value in geometry.texture_names
        )
        parameters = tuple(
            (str(slot or ""), texture_name)
            for slot, texture_name in raw_parameters
            if isinstance(texture_name, str) and texture_name.strip()
        )
        source_key = (
            geometry.material_index, identity, semantic, tuple(geometry.texture_names),
            parameters,
        )
        key = material_keys.get(source_key)
        if key is None:
            key = f"mat_{len(material_keys):04d}"
            material_keys[source_key] = key
            bindings = []
            for slot, texture_name in parameters:
                texture_path = resolved_assets.get(texture_name.casefold())
                bindings.append({
                    "slot": slot,
                    "name": texture_name,
                    "role": _texture_role(slot, texture_name),
                    "path": (
                        texture_path.relative_to(target).as_posix()
                        if texture_path is not None and _is_within(texture_path, target)
                        else None
                    ),
                })
            material_records.append({
                "key": key,
                "source_name": identity,
                "semantic": semantic,
                "color": [channel / 255.0 for channel in color],
                "texture_names": list(geometry.texture_names),
                "texture_bindings": bindings,
                **properties,
            })
        geometry_materials.append(key)

    obj_path = target / "model.obj"
    mtl_path = target / "model.mtl"
    manifest_path = target / "scene.json"
    digest = hashlib.sha256()

    with mtl_path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("# ALLIN1 temporary material interchange\n")
        for record in material_records:
            red, green, blue = record["color"]
            stream.write(f"newmtl {record['key']}\n")
            stream.write(f"Kd {red:.8f} {green:.8f} {blue:.8f}\n")
            stream.write(f"Ks {record['metallic']:.8f} {record['metallic']:.8f} {record['metallic']:.8f}\n")
            stream.write(f"Ns {(1.0 - record['roughness']) * 900.0 + 10.0:.4f}\n")
            stream.write(f"d {record['alpha']:.8f}\nillum 2\n\n")

    exported_triangles = 0
    skipped_triangles = 0
    vertex_offset = 0
    texcoord_offset = 0
    with obj_path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("# ALLIN1 temporary decoded-geometry interchange\n")
        stream.write("mtllib model.mtl\n")
        for geometry_index, (geometry, material_key) in enumerate(
            zip(selected, geometry_materials)
        ):
            stream.write(f"o geometry_{geometry_index:05d}\n")
            stream.write(f"usemtl {material_key}\n")
            for vertex in geometry.vertices:
                if len(vertex) != 3 or not all(math.isfinite(value) for value in vertex):
                    raise CompiledRenderError(
                        "invalid_geometry", "Model geometry contains a non-finite vertex",
                        {"geometry": geometry_index},
                    )
                line = f"v {vertex[0]:.9g} {vertex[1]:.9g} {vertex[2]:.9g}\n"
                stream.write(line)
                digest.update(line.encode("ascii"))
            has_texcoords = len(getattr(geometry, "texcoords", ())) == len(
                geometry.vertices
            )
            if has_texcoords:
                for uv in geometry.texcoords:
                    # GTA/DirectX texture coordinates use a top-left image origin;
                    # OBJ/Blender expects V from the bottom edge.
                    line = f"vt {uv[0]:.9g} {1.0 - uv[1]:.9g}\n"
                    stream.write(line)
                    digest.update(line.encode("ascii"))
            size = len(geometry.vertices)
            for triangle in geometry.triangles:
                if (
                    len(triangle) != 3
                    or len(set(triangle)) != 3
                    or any(
                        isinstance(index, bool) or not isinstance(index, int)
                        or index < 0 or index >= size
                        for index in triangle
                    )
                ):
                    skipped_triangles += 1
                    continue
                indices = tuple(vertex_offset + index + 1 for index in triangle)
                if has_texcoords:
                    uv_indices = tuple(texcoord_offset + index + 1 for index in triangle)
                    line = (
                        f"f {indices[0]}/{uv_indices[0]} "
                        f"{indices[1]}/{uv_indices[1]} "
                        f"{indices[2]}/{uv_indices[2]}\n"
                    )
                else:
                    line = f"f {indices[0]} {indices[1]} {indices[2]}\n"
                stream.write(line)
                digest.update(line.encode("ascii"))
                exported_triangles += 1
            vertex_offset += size
            if has_texcoords:
                texcoord_offset += size
    if not exported_triangles:
        raise CompiledRenderError(
            "empty_scene", "The decoded model scene has no valid triangles to render",
        )

    resolved_texture_names = {
        binding["name"].casefold()
        for record in material_records
        for binding in record["texture_bindings"]
        if binding["path"]
    }
    unresolved_texture_names = tuple(sorted({
        binding["name"]
        for record in material_records
        for binding in record["texture_bindings"]
        if not binding["path"]
    }, key=str.casefold))
    textured_material_count = sum(
        any(binding["path"] for binding in record["texture_bindings"])
        for record in material_records
    )
    manifest = {
        "schema": 1,
        "source_name": scene.name,
        "obj": obj_path.name,
        "bounds": {"minimum": list(minima), "maximum": list(maxima)},
        "counts": {
            "geometries": len(selected), "vertices": vertex_count,
            "triangles": exported_triangles, "skipped_triangles": skipped_triangles,
            "materials": len(material_records),
            "textures": len(resolved_texture_names),
            "textured_materials": textured_material_count,
        },
        "materials": material_records,
        "selection": {"lod": lod or "All", "component": component or "All"},
        "geometry_sha256": digest.hexdigest(),
        "unresolved_texture_names": list(unresolved_texture_names),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8",
    )
    combined_digest = hashlib.sha256()
    for path in (obj_path, mtl_path, manifest_path):
        combined_digest.update(path.read_bytes())
    return RenderInterchange(
        directory=target, obj_path=obj_path, mtl_path=mtl_path,
        manifest_path=manifest_path, vertex_count=vertex_count,
        triangle_count=exported_triangles, geometry_count=len(selected),
        material_count=len(material_records), texture_count=len(resolved_texture_names),
        textured_material_count=textured_material_count,
        unresolved_texture_names=unresolved_texture_names,
        sha256=combined_digest.hexdigest(),
    )


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_output_path(
    output_path: str | Path, protected_roots: Iterable[str | Path],
) -> Path:
    output = Path(output_path).expanduser()
    if output.suffix.casefold() != ".png":
        raise CompiledRenderError("invalid_output", "Compiled render output must be a PNG file")
    try:
        parent = output.parent.resolve(strict=True)
    except OSError as exc:
        raise CompiledRenderError(
            "invalid_output", "Compiled render output folder does not exist",
            {"path": str(output.parent)},
        ) from exc
    if not parent.is_dir():
        raise CompiledRenderError("invalid_output", "Compiled render output folder is not a directory")
    resolved = parent / output.name
    for raw_root in protected_roots:
        try:
            root = Path(raw_root).expanduser().resolve(strict=True)
        except OSError:
            continue
        if _is_within(resolved, root):
            raise CompiledRenderError(
                "protected_output",
                "Compiled renders cannot be written inside a game or package source folder",
                {"output": str(resolved), "protected_root": str(root)},
            )
    return resolved


def _emit(
    callback: ProgressCallback | None, stage: str, fraction: float, message: str,
) -> None:
    if callback is not None:
        callback(CompiledRenderProgress(stage, fraction, message))


def _cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise CompiledRenderError("render_cancelled", "The compiled render was cancelled")


def compile_vehicle_render(
    scene: NativeModelScene, output_path: str | Path, *,
    settings: CompiledRenderSettings | None = None,
    blender_executable: str | Path | None = None,
    texture_dictionary: str | Path | None = None,
    edition: str = "Enhanced",
    gta_path: str | Path | None = None,
    yaw: float = 34.0, pitch: float = 18.0,
    lod: str | None = None, component: str | None = None,
    protected_roots: Iterable[str | Path] = (),
    cancel_event: threading.Event | None = None,
    progress: ProgressCallback | None = None,
    process_runner: BlenderProcessRunner | None = None,
) -> CompiledRenderResult:
    """Compile a decoded vehicle scene into a studio-quality PNG using Blender."""

    started = time.monotonic()
    configured = settings or CompiledRenderSettings()
    _finite_between(yaw, -3600.0, 3600.0, "camera yaw")
    _finite_between(pitch, -89.0, 89.0, "camera pitch")
    guarded_roots = list(protected_roots)
    if texture_dictionary is not None:
        guarded_roots.append(Path(texture_dictionary).expanduser().parent)
    output = _validate_output_path(output_path, guarded_roots)
    runner = process_runner or _default_process_runner
    _cancelled(cancel_event)
    _emit(progress, "validate", 0.04, "Checking Blender and render settings")
    installation = detect_blender(
        blender_executable, process_runner=runner,
    )
    if installation is None:
        raise CompiledRenderError(
            "blender_not_found",
            "Blender was not found. Install Blender or choose its executable in render settings.",
        )
    _cancelled(cancel_event)

    with tempfile.TemporaryDirectory(prefix="allin1-compiled-render-") as raw_workspace:
        workspace = Path(raw_workspace).resolve(strict=True)
        texture_export: _TextureDictionaryExport | None = None
        if texture_dictionary is not None:
            _emit(
                progress, "textures", 0.09,
                "Decoding the linked texture dictionary in the isolated workspace",
            )
            referenced_names = {
                value for geometry in _selected_geometries(
                    scene, lod=lod, component=component,
                ) for value in geometry.texture_names
            }
            texture_export = _export_texture_dictionary(
                texture_dictionary, workspace, referenced_names=referenced_names,
                edition=edition, gta_path=gta_path,
            )
        _emit(progress, "export", 0.14, "Exporting decoded geometry to an isolated workspace")
        interchange = export_render_interchange(
            scene, workspace, lod=lod, component=component,
            texture_assets=(texture_export.assets if texture_export else None),
        )
        _cancelled(cancel_event)
        render_output = workspace / "compiled-render.png"
        result_json = workspace / "render-result.json"
        render_config = workspace / "render-config.json"
        script_path = workspace / "compile_scene.py"
        payload = {
            "schema": 1,
            "interchange": str(interchange.manifest_path),
            "obj": str(interchange.obj_path),
            "output": str(render_output),
            "result": str(result_json),
            "settings": {
                **asdict(configured),
                "samples": configured.effective_samples,
                "yaw": float(yaw),
                "pitch": float(pitch),
                "transparent": configured.transparent or configured.background == "transparent",
            },
        }
        render_config.write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8",
        )
        script_path.write_text(_BLENDER_COMPILE_SCRIPT, encoding="utf-8")
        command = [
            str(installation.executable), "--background", "--factory-startup",
            "--disable-autoexec", "--python-exit-code", "9",
            "--python", str(script_path), "--", str(render_config),
        ]
        _emit(progress, "launch", 0.22, "Starting isolated Blender render")
        _cancelled(cancel_event)
        try:
            render_timeout = (
                1800.0
                if configured.width * configured.height > 7680 * 4320
                else (900.0 if configured.quality == "maximum" else 420.0)
            )
            completed = runner(
                command, cwd=workspace,
                timeout=render_timeout,
                cancel_event=cancel_event,
            )
        except CompiledRenderError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            raise CompiledRenderError(
                "blender_launch_failed", "Blender could not be started",
                {"error": str(exc)},
            ) from exc
        _emit(progress, "render", 0.82, "Blender finished; validating the compiled frame")
        if completed.returncode != 0:
            raise CompiledRenderError(
                "blender_render_failed", "Blender could not compile the render",
                {
                    "returncode": completed.returncode,
                    "stdout": (completed.stdout or "")[-4000:],
                    "stderr": (completed.stderr or "")[-4000:],
                },
            )
        _cancelled(cancel_event)
        if not result_json.is_file():
            raise CompiledRenderError(
                "missing_render_result", "Blender did not produce structured render metadata",
                {"stdout": (completed.stdout or "")[-2000:]},
            )
        try:
            blender_metadata = json.loads(result_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CompiledRenderError(
                "invalid_render_result", "Blender produced invalid render metadata",
            ) from exc
        if not render_output.is_file() or render_output.stat().st_size <= 8:
            raise CompiledRenderError("missing_render", "Blender did not produce a PNG render")
        try:
            # The explicit dimension and pixel guards above make the supported
            # 16K frame safe to inspect.  Pillow's generic decompression-bomb
            # warning is intentionally lower than that bounded output size.
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", Image.DecompressionBombWarning)
                with Image.open(render_output) as image:
                    image.verify()
                with Image.open(render_output) as image:
                    if image.format != "PNG" or image.size != (
                        configured.width, configured.height,
                    ):
                        raise CompiledRenderError(
                            "invalid_render",
                            "Blender produced a render with an unexpected format or resolution",
                            {"format": image.format, "size": image.size},
                        )
        except CompiledRenderError:
            raise
        except (OSError, ValueError) as exc:
            raise CompiledRenderError("invalid_render", "Blender produced an unreadable PNG") from exc
        _cancelled(cancel_event)
        _emit(progress, "verify", 0.93, "Saving the verified PNG")
        temporary_output: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=f".{output.stem}-", suffix=".png", dir=output.parent,
                delete=False,
            ) as stream:
                temporary_output = Path(stream.name)
                with render_output.open("rb") as source:
                    shutil.copyfileobj(source, stream)
            temporary_output.replace(output)
        finally:
            if temporary_output is not None and temporary_output.exists():
                temporary_output.unlink(missing_ok=True)

    elapsed = time.monotonic() - started
    metadata = {
        "backend": "Blender headless",
        "blender_version": installation.version,
        "blender_source": installation.source,
        "engine": configured.engine,
        "requested_device": configured.device,
        "actual_device": blender_metadata.get("device", "unknown"),
        "quality": configured.quality,
        "samples": configured.effective_samples,
        "light_rig": configured.light_rig,
        "background": configured.background,
        "transparent": configured.transparent or configured.background == "transparent",
        "geometry_count": interchange.geometry_count,
        "vertex_count": interchange.vertex_count,
        "triangle_count": interchange.triangle_count,
        "material_count": interchange.material_count,
        "texture_count": interchange.texture_count,
        "textured_material_count": interchange.textured_material_count,
        "unresolved_texture_names": list(interchange.unresolved_texture_names),
        "texture_dictionary": (
            {
                "source": str(texture_export.source),
                "sha256": _sha256_file(texture_export.source),
                "dictionary_texture_count": texture_export.dictionary_texture_count,
                "exported_texture_count": texture_export.exported_texture_count,
                "warnings": list(texture_export.warnings),
            }
            if texture_export is not None else None
        ),
        "interchange_sha256": interchange.sha256,
        "selection": {"lod": lod or "All", "component": component or "All"},
        "camera": {"yaw": float(yaw), "pitch": float(pitch), "lens_mm": configured.lens_mm},
        "render": blender_metadata,
        "fidelity": (
            "decoded geometry with linked YTD texture pixels, UV mapping, generated physically "
            "based shader approximations, and studio lighting; game shader programs and skinning "
            "are not reproduced"
            if interchange.texture_count else
            "decoded geometry with generated physically based materials and studio lighting; "
            "no linked texture pixels were resolved"
        ),
    }
    _emit(progress, "complete", 1.0, "Compiled render complete")
    return CompiledRenderResult(
        output_path=output, width=configured.width, height=configured.height,
        elapsed_seconds=elapsed, metadata=metadata,
    )


__all__ = [
    "BlenderInstallation",
    "BlenderProcessRunner",
    "CompiledRenderError",
    "CompiledRenderProgress",
    "CompiledRenderResult",
    "CompiledRenderSettings",
    "MAX_COMPILED_PIXELS",
    "MAX_COMPILED_RESOLUTION",
    "ProgressCallback",
    "RenderInterchange",
    "compile_vehicle_render",
    "detect_blender",
    "export_render_interchange",
]


# This script is data-independent.  All paths and settings arrive through one
# generated JSON file after ``--``; no package-controlled text is interpolated
# into executable Python or the subprocess command.
_BLENDER_COMPILE_SCRIPT = r'''import json
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector


def load_payload():
    marker = sys.argv.index("--")
    path = Path(sys.argv[marker + 1]).resolve(strict=True)
    return json.loads(path.read_text(encoding="utf-8"))


def set_input(node, names, value):
    for name in names:
        socket = node.inputs.get(name)
        if socket is not None:
            socket.default_value = value
            return True
    return False


def point_at(obj, target):
    obj.rotation_euler = (Vector(target) - obj.location).to_track_quat("-Z", "Y").to_euler()


def add_area(name, location, target, energy, size, color, contact_shadows):
    data = bpy.data.lights.new(name=name, type="AREA")
    data.energy = energy
    data.shape = "DISK"
    data.size = size
    data.color = color
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.location = location
    point_at(obj, target)
    if hasattr(data, "use_shadow"):
        data.use_shadow = True
    if contact_shadows and hasattr(data, "use_contact_shadow"):
        data.use_contact_shadow = True
    return obj


def orbit_location(target, radius, azimuth, elevation):
    az = math.radians(azimuth)
    el = math.radians(elevation)
    return (
        target[0] + radius * math.cos(el) * math.sin(az),
        target[1] - radius * math.cos(el) * math.cos(az),
        target[2] + radius * math.sin(el),
    )


started = time.monotonic()
payload = load_payload()
settings = payload["settings"]
manifest = json.loads(Path(payload["interchange"]).read_text(encoding="utf-8"))
interchange_root = Path(payload["interchange"]).resolve(strict=True).parent
bpy.ops.wm.read_factory_settings(use_empty=True)

obj_path = str(Path(payload["obj"]).resolve(strict=True))
if hasattr(bpy.ops.wm, "obj_import"):
    try:
        bpy.ops.wm.obj_import(filepath=obj_path, forward_axis="NEGATIVE_Y", up_axis="Z")
    except (TypeError, ValueError):
        # Blender 3.x briefly exposed the same operator with suffixed enum names.
        bpy.ops.wm.obj_import(
            filepath=obj_path, forward_axis="NEGATIVE_Y_FORWARD", up_axis="Z_UP",
        )
else:
    try:
        bpy.ops.preferences.addon_enable(module="io_scene_obj")
    except Exception:
        pass
    bpy.ops.import_scene.obj(filepath=obj_path, axis_forward="-Y", axis_up="Z")

mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
if not mesh_objects:
    raise RuntimeError("OBJ import did not create mesh objects")
for obj in mesh_objects:
    for polygon in obj.data.polygons:
        polygon.use_smooth = True

image_cache = {}
texture_load_failures = []


def texture_image(binding, non_color=False):
    relative = binding.get("path")
    if not relative:
        return None
    candidate = (interchange_root / relative).resolve(strict=True)
    try:
        candidate.relative_to(interchange_root)
    except ValueError:
        raise RuntimeError("Texture dependency escaped the isolated interchange workspace")
    # Blender stores color-space policy on the image datablock. Keep color and
    # data uses separate when one authored texture is reused by multiple slots.
    key = (str(candidate), bool(non_color))
    image = image_cache.get(key)
    if image is None:
        try:
            image = bpy.data.images.load(str(candidate), check_existing=False)
        except Exception as exc:
            texture_load_failures.append({"name": binding.get("name", ""), "error": str(exc)})
            return None
        image_cache[key] = image
    if non_color:
        try:
            image.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
    return image


for record in manifest["materials"]:
    material = bpy.data.materials.get(record["key"])
    if material is None:
        continue
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new("ShaderNodeOutputMaterial")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    color = tuple(record["color"]) + (float(record["alpha"]),)
    set_input(shader, ("Base Color",), color)
    set_input(shader, ("Metallic",), float(record["metallic"]))
    set_input(shader, ("Roughness",), float(record["roughness"]))
    set_input(shader, ("Alpha",), float(record["alpha"]))
    if record["semantic"] == "glass":
        set_input(shader, ("Transmission Weight", "Transmission"), 0.58)
        set_input(shader, ("IOR",), 1.46)
    if record["semantic"] == "paint":
        set_input(shader, ("Coat Weight", "Clearcoat"), 0.42)
        set_input(shader, ("Coat Roughness", "Clearcoat Roughness"), 0.12)
    if float(record["emission"]) > 0.0:
        set_input(shader, ("Emission Color", "Emission"), color)
        set_input(shader, ("Emission Strength",), float(record["emission"]))
    bindings = [item for item in record.get("texture_bindings", []) if item.get("path")]
    by_role = {}
    for binding in bindings:
        by_role.setdefault(binding.get("role", "unclassified"), binding)
    diffuse_binding = by_role.get("diffuse") or by_role.get("unclassified")
    if diffuse_binding is not None:
        image = texture_image(diffuse_binding)
        if image is not None:
            texture = nodes.new("ShaderNodeTexImage")
            texture.name = "ALLIN1 Diffuse · " + diffuse_binding.get("name", "")
            texture.image = image
            socket = shader.inputs.get("Base Color")
            if socket is not None:
                material.node_tree.links.new(texture.outputs["Color"], socket)
            if record["semantic"] == "light" or float(record["emission"]) > 0.0:
                emission_socket = shader.inputs.get("Emission Color") or shader.inputs.get("Emission")
                if emission_socket is not None:
                    material.node_tree.links.new(texture.outputs["Color"], emission_socket)
    normal_binding = by_role.get("normal")
    if normal_binding is not None:
        image = texture_image(normal_binding, non_color=True)
        if image is not None:
            texture = nodes.new("ShaderNodeTexImage")
            texture.name = "ALLIN1 Normal · " + normal_binding.get("name", "")
            texture.image = image
            normal = nodes.new("ShaderNodeNormalMap")
            normal.inputs["Strength"].default_value = 0.65
            material.node_tree.links.new(texture.outputs["Color"], normal.inputs["Color"])
            socket = shader.inputs.get("Normal")
            if socket is not None:
                material.node_tree.links.new(normal.outputs["Normal"], socket)
    specular_binding = by_role.get("specular")
    if specular_binding is not None:
        image = texture_image(specular_binding, non_color=True)
        if image is not None:
            texture = nodes.new("ShaderNodeTexImage")
            texture.name = "ALLIN1 Specular · " + specular_binding.get("name", "")
            texture.image = image
            socket = shader.inputs.get("Specular IOR Level") or shader.inputs.get("Specular")
            if socket is not None:
                material.node_tree.links.new(texture.outputs["Color"], socket)
    material.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    if float(record["alpha"]) < 1.0:
        if hasattr(material, "surface_render_method"):
            try:
                material.surface_render_method = "DITHERED"
            except Exception:
                pass
        elif hasattr(material, "blend_method"):
            material.blend_method = "BLEND"
        material.diffuse_color = color

minimum = Vector(manifest["bounds"]["minimum"])
maximum = Vector(manifest["bounds"]["maximum"])
center = (minimum + maximum) * 0.5
dimensions = maximum - minimum
radius = max(dimensions.length * 0.5, 0.5)
target = Vector((center.x, center.y, minimum.z + dimensions.z * 0.42))

scene = bpy.context.scene
requested_engine = settings["engine"]
if requested_engine == "cycles":
    try:
        scene.render.engine = "CYCLES"
        scene.cycles.samples = int(settings["samples"])
        scene.cycles.use_denoising = True
    except Exception:
        raise RuntimeError("This Blender installation does not provide the Cycles engine")
    requested_device = settings.get("device", "auto")
    actual_device = "CPU"
    if requested_device != "cpu":
        available_gpu = False
        try:
            preferences = bpy.context.preferences.addons["cycles"].preferences
            # Backends are tried in a stable preference order. Unsupported
            # enum values raise and are simply skipped by Blender.
            for backend in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
                try:
                    preferences.compute_device_type = backend
                    preferences.get_devices()
                except Exception:
                    continue
                candidates = [
                    device for device in preferences.devices
                    if getattr(device, "type", "CPU") != "CPU"
                ]
                if not candidates:
                    continue
                for device in candidates:
                    device.use = True
                scene.cycles.device = "GPU"
                actual_device = backend + " GPU"
                available_gpu = True
                break
        except Exception:
            available_gpu = False
        if requested_device == "gpu" and not available_gpu:
            raise RuntimeError("GPU rendering was requested, but Blender found no supported Cycles GPU device")
    if actual_device == "CPU":
        scene.cycles.device = "CPU"
else:
    try:
        scene.render.engine = "BLENDER_EEVEE_NEXT"
    except Exception:
        scene.render.engine = "BLENDER_EEVEE"
    eevee = getattr(scene, "eevee", None)
    if eevee is not None and hasattr(eevee, "taa_render_samples"):
        eevee.taa_render_samples = int(settings["samples"])
    actual_device = "GPU raster"

scene.render.resolution_x = int(settings["width"])
scene.render.resolution_y = int(settings["height"])
scene.render.resolution_percentage = 100
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA" if settings["transparent"] else "RGB"
scene.render.film_transparent = bool(settings["transparent"])
scene.render.filepath = str(Path(payload["output"]).resolve())

world = bpy.data.worlds.new("ALLIN1 Render World")
scene.world = world
world.use_nodes = True
background_node = world.node_tree.nodes.get("Background")
colors = {
    "studio_dark": (0.018, 0.028, 0.023, 1.0),
    "studio_light": (0.58, 0.62, 0.60, 1.0),
}
if settings["background"] == "custom":
    raw = settings["background_color"].lstrip("#")
    world_color = tuple(int(raw[i:i + 2], 16) / 255.0 for i in (0, 2, 4)) + (1.0,)
else:
    world_color = colors.get(settings["background"], colors["studio_dark"])
background_node.inputs["Color"].default_value = world_color
background_node.inputs["Strength"].default_value = 0.22

camera_data = bpy.data.cameras.new("ALLIN1 Camera")
camera_data.lens = float(settings["lens_mm"])
camera = bpy.data.objects.new("ALLIN1 Camera", camera_data)
bpy.context.collection.objects.link(camera)
aspect = max(settings["width"] / settings["height"], settings["height"] / settings["width"])
# Keep the complete asset and its cast shadow inside frame.  The previous 2.05
# fit was too tight for long vehicle fragments when viewed three-quarter-on;
# the wider production margin also leaves room for asymmetric tuning parts.
distance = radius * (3.00 + max(0.0, aspect - 1.0) * 0.18) * (settings["lens_mm"] / 50.0)
camera.location = orbit_location(target, distance, float(settings["yaw"]), float(settings["pitch"]))
point_at(camera, target)
camera_data.dof.use_dof = False
scene.camera = camera

rotation = float(settings["light_rotation_deg"])
strength = float(settings["light_strength"])
rig = settings["light_rig"]
rig_values = {
    "studio": ((42, 52, 1250, 0.34), (-58, 25, 520, 0.62), (158, 44, 1050, 0.32)),
    "outdoor": ((28, 62, 1500, 0.42), (-72, 32, 700, 0.58), (142, 34, 720, 0.38)),
    "dramatic": ((48, 38, 1750, 0.24), (-62, 18, 240, 0.72), (168, 48, 1900, 0.22)),
    "neutral": ((35, 48, 900, 0.48), (-45, 42, 780, 0.52), (165, 40, 820, 0.46)),
}
for index, (azimuth, elevation, power, size_factor) in enumerate(rig_values[rig]):
    add_area(
        ("Key", "Fill", "Rim")[index],
        orbit_location(target, radius * 3.0, azimuth + rotation, elevation),
        target, power * strength, max(radius * size_factor, 0.4),
        ((1.0, 0.86, 0.72), (0.72, 0.84, 1.0), (0.74, 0.88, 1.0))[index],
        bool(settings.get("contact_shadows", True)),
    )

ground_enabled = bool(settings["ground_plane"]) and not bool(settings["transparent"])
if ground_enabled:
    bpy.ops.mesh.primitive_plane_add(
        size=max(dimensions.x, dimensions.y, 1.0) * 4.0,
        location=(center.x, center.y, minimum.z - max(dimensions.z * 0.002, 0.002)),
    )
    ground = bpy.context.object
    ground.name = "ALLIN1 Ground"
    material = bpy.data.materials.new("ALLIN1 Ground Material")
    material.use_nodes = True
    shader = material.node_tree.nodes.get("Principled BSDF")
    base = (0.035, 0.045, 0.040, 1.0) if settings["background"] != "studio_light" else (0.32, 0.34, 0.33, 1.0)
    set_input(shader, ("Base Color",), base)
    set_input(shader, ("Roughness",), 0.72)
    ground.data.materials.append(material)

try:
    scene.view_settings.look = "AgX - Medium High Contrast"
except Exception:
    try:
        scene.view_settings.look = "Medium High Contrast"
    except Exception:
        pass

bpy.ops.render.render(write_still=True)
result = {
    "schema": 1,
    "blender_version": bpy.app.version_string,
    "engine": scene.render.engine,
    "device": actual_device,
    "samples": int(settings["samples"]),
    "light_rig": rig,
    "ground_plane": ground_enabled,
    "elapsed_seconds": round(time.monotonic() - started, 4),
    "loaded_texture_images": len(image_cache),
    "texture_load_failures": texture_load_failures,
}
Path(payload["result"]).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
'''
