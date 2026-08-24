"""Inspection and guarded XML round-tripping of native GTA V assets.

The lightweight parsers in this module always work and intentionally stop at
well-defined headers.  When the pinned RpfPatcher/CodeWalker helper is present,
supported RAGE resources are additionally converted to their structured XML
representation and texture dictionaries receive a visual contact sheet. Supported
resources can also be exported into snapshot-backed editing workspaces and rebuilt
only after the result successfully reparses through CodeWalker.
"""

from __future__ import annotations

import colorsys
import hashlib
import io
import json
import math
import re
import shutil
import struct
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from lxml import etree
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from allin1_sdk.gxt2_workspace import Gxt2Workspace
from allin1_sdk.processes import run_hidden


NATIVE_XML_SUFFIXES = frozenset({
    ".awc", ".gxt2", ".pso", ".rel", ".ybn", ".ycd", ".ydd", ".ydr",
    ".yed", ".yfd", ".yft", ".ymap", ".ymf", ".ymt", ".ynd",
    ".ynv", ".ypt", ".ytd", ".ytyp", ".yvr", ".ywr",
})
NATIVE_XML_IMPORT_SUFFIXES = frozenset({
    ".awc", ".pso", ".rel", ".ybn", ".ycd", ".ydd", ".ydr", ".yed",
    ".yfd", ".yft", ".ymap", ".ymf", ".ymt", ".ynd", ".ynv",
    ".ypt", ".ytd", ".ytyp", ".yvr", ".ywr",
})
NATIVE_ASSET_SUFFIXES = NATIVE_XML_SUFFIXES | frozenset({
    ".awc", ".gfx", ".rel", ".rpf", ".ycd", ".yed", ".yfd",
    ".ymf", ".ynd", ".ynv", ".ypt", ".yvr", ".ywr",
})
MAX_NATIVE_PREVIEW_BYTES = 128 * 1024 * 1024
MAX_NATIVE_WORKSPACE_FILES = 10_000
MAX_NATIVE_WORKSPACE_BYTES = 2 * 1024 * 1024 * 1024
NATIVE_WORKSPACE_SCHEMA = 1
MODEL_PREVIEW_SUFFIXES = frozenset({".ydr", ".ydd", ".yft"})
COLLISION_PREVIEW_SUFFIXES = frozenset({".ybn"})
MAP_PREVIEW_SUFFIXES = frozenset({".ymap"})
NAVMESH_PREVIEW_SUFFIXES = frozenset({".ynv"})
PATH_PREVIEW_SUFFIXES = frozenset({".ynd"})
ARCHETYPE_PREVIEW_SUFFIXES = frozenset({".ytyp"})
AUDIO_PREVIEW_SUFFIXES = frozenset({".awc"})
MAX_MODEL_XML_BYTES = 192 * 1024 * 1024
MAX_MODEL_VERTICES = 1_000_000
MAX_MODEL_TRIANGLES = 1_000_000
MAX_RENDERED_TRIANGLES = 45_000
INTERACTIVE_RENDERED_TRIANGLES = 6_000
INTERACTIVE_SILHOUETTE_POINTS = 2_048
MODEL_RENDER_MODES = frozenset({"materials", "shaded", "wireframe"})
MODEL_RENDER_QUALITIES = {
    "interactive": INTERACTIVE_RENDERED_TRIANGLES,
    "final": MAX_RENDERED_TRIANGLES,
    "full": MAX_MODEL_TRIANGLES,
}

# CodeWalker can emit unresolved shader identities as their lower-case Jenkins
# hash.  Keep this small catalog deterministic and source-controlled so reports
# remain useful even when a game build does not ship a readable shader-name
# table.  Names are verified against the same hash algorithm used by RAGE.
KNOWN_SHADER_HASH_NAMES = {
    "59b24d3d": "weapon_emissivestrong_alpha.sps",
}


def resolve_shader_name(value: str) -> str:
    """Return a stable authored shader name for a known CodeWalker hash."""
    authored = str(value or "").strip()
    match = re.fullmatch(r"hash_([0-9a-fA-F]{8})", authored)
    if match is None:
        return authored
    return KNOWN_SHADER_HASH_NAMES.get(match.group(1).casefold(), authored)
MAX_MAP_ENTITIES = 250_000
MAX_RENDERED_MAP_ENTITIES = 80_000
MAX_NAV_POLYGONS = 300_000
MAX_NAV_VERTICES = 2_000_000
MAX_NAV_PORTALS = 300_000
MAX_NAV_POINTS = 300_000
MAX_RENDERED_NAV_POLYGONS = 60_000
MAX_PATH_NODES = 500_000
MAX_PATH_LINKS = 2_000_000
MAX_PATH_JUNCTIONS = 250_000
MAX_RENDERED_PATH_NODES = 90_000
MAX_RENDERED_PATH_LINKS = 140_000
MAX_ARCHETYPES = 250_000
MAX_RENDERED_ARCHETYPES = 18


@dataclass(frozen=True)
class _ConvertedAsset:
    structured_text: str
    image_png: bytes | None
    texture_count: int
    metadata: dict[str, Any] = field(default_factory=dict)
    conversion_error: str | None = None
    model_scene: NativeModelScene | None = None


@dataclass(frozen=True)
class _ModelGeometry:
    vertices: tuple[tuple[float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]
    lod: str
    component: str = "Default drawable"
    material_index: int | None = None
    material_name: str = ""
    texture_names: tuple[str, ...] = ()
    texcoords: tuple[tuple[float, float], ...] = ()
    texture_parameters: tuple[tuple[str, str], ...] = ()


_ModelRenderBounds = tuple[
    tuple[float, float, float], tuple[float, float, float], int,
]
_PreparedInteractiveTriangle = tuple[
    int, int, tuple[int, int, int],
    tuple[float, float, float], tuple[float, float, float],
    tuple[float, float, float], float,
]


def _model_geometry_bounds(
    geometries: tuple[_ModelGeometry, ...] | list[_ModelGeometry],
) -> _ModelRenderBounds:
    vertex_count = sum(len(geometry.vertices) for geometry in geometries)
    if not vertex_count:
        raise ValueError("Model scene does not contain any vertices")
    minima = [math.inf, math.inf, math.inf]
    maxima = [-math.inf, -math.inf, -math.inf]
    for geometry in geometries:
        for point in geometry.vertices:
            for axis in range(3):
                minima[axis] = min(minima[axis], point[axis])
                maxima[axis] = max(maxima[axis], point[axis])
    return (
        (minima[0], minima[1], minima[2]),
        (maxima[0], maxima[1], maxima[2]),
        vertex_count,
    )


@dataclass(frozen=True)
class NativeModelMaterial:
    """One shader/material record referenced by decoded model geometry."""

    index: int
    name: str
    texture_names: tuple[str, ...]
    texture_parameters: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class NativeModelComponent:
    """Aggregated drawable component surfaced by the diagnostic model scene."""

    name: str
    lod: str
    geometry_count: int
    vertex_count: int
    triangle_count: int
    material_names: tuple[str, ...]
    texture_names: tuple[str, ...]


@dataclass(frozen=True)
class NativeModelScene:
    """Bounded decoded geometry that can be rendered from multiple camera views."""

    name: str
    geometries: tuple[_ModelGeometry, ...]
    metadata: dict[str, Any] = field(default_factory=dict)
    materials: tuple[NativeModelMaterial, ...] = ()
    _render_bounds_cache: dict[tuple[str, str], _ModelRenderBounds] = field(
        default_factory=dict, init=False, repr=False, compare=False,
    )
    _render_material_cache: tuple[
        tuple[str, str, tuple[int, int, int]], ...
    ] = field(default=(), init=False, repr=False, compare=False)
    _interactive_triangle_cache: dict[
        tuple[str, str, int], tuple[_PreparedInteractiveTriangle, ...]
    ] = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Frame calculations are invariant across camera angles. Computing the
        # full-scene bounds once keeps orbit renders from walking hundreds of
        # thousands of vertices before drawing their bounded triangle sample.
        if self.geometries:
            self._render_bounds_cache[("all", "all")] = _model_geometry_bounds(
                self.geometries
            )
        # Material classification depends only on decoded geometry. Cache it on
        # the immutable scene so orbit frames do not repeatedly scan shader and
        # texture names or hash unknown material identities.
        object.__setattr__(
            self, "_render_material_cache",
            tuple(_model_material_identity(item) for item in self.geometries),
        )

    @property
    def lods(self) -> tuple[str, ...]:
        return tuple(sorted(
            {item.lod for item in self.geometries}, key=str.casefold,
        ))

    @property
    def components(self) -> tuple[NativeModelComponent, ...]:
        grouped: dict[tuple[str, str], list[_ModelGeometry]] = {}
        for geometry in self.geometries:
            grouped.setdefault((geometry.component, geometry.lod), []).append(geometry)
        return tuple(
            NativeModelComponent(
                name=name,
                lod=lod,
                geometry_count=len(items),
                vertex_count=sum(len(item.vertices) for item in items),
                triangle_count=sum(len(item.triangles) for item in items),
                material_names=tuple(sorted(
                    {item.material_name for item in items if item.material_name},
                    key=str.casefold,
                )),
                texture_names=tuple(sorted(
                    {value for item in items for value in item.texture_names},
                    key=str.casefold,
                )),
            )
            for (name, lod), items in grouped.items()
        )

    def render(
        self, *, yaw: float = 34.0, pitch: float = 24.0,
        lod: str | None = None, component: str | None = None,
        render_mode: str = "shaded", quality: str = "final",
        triangle_budget: int | None = None,
    ) -> tuple[bytes, dict[str, Any]]:
        """Render a PNG while preserving the established public contract."""
        image, metadata = self.render_image(
            yaw=yaw, pitch=pitch, lod=lod, component=component,
            render_mode=render_mode, quality=quality,
            triangle_budget=triangle_budget,
        )
        return _encode_model_render(
            image, str(metadata["model_render_quality"]),
        ), metadata

    def render_image(
        self, *, yaw: float = 34.0, pitch: float = 24.0,
        lod: str | None = None, component: str | None = None,
        render_mode: str = "shaded", quality: str = "final",
        triangle_budget: int | None = None,
    ) -> tuple[Image.Image, dict[str, Any]]:
        """Render a fresh RGB image for in-process interactive viewports."""
        if not math.isfinite(yaw) or not math.isfinite(pitch):
            raise ValueError("Model camera angles must be finite")
        if not isinstance(render_mode, str):
            raise ValueError("Model render mode must be a string")
        normalized_mode = render_mode.strip().casefold()
        if normalized_mode not in MODEL_RENDER_MODES:
            choices = ", ".join(sorted(MODEL_RENDER_MODES))
            raise ValueError(f"Model render mode must be one of: {choices}")
        if not isinstance(quality, str):
            raise ValueError("Model render quality must be a string")
        normalized_quality = quality.strip().casefold()
        if normalized_quality not in MODEL_RENDER_QUALITIES:
            choices = ", ".join(MODEL_RENDER_QUALITIES)
            raise ValueError(f"Model render quality must be one of: {choices}")
        maximum_budget = (
            MAX_MODEL_TRIANGLES
            if normalized_quality == "full"
            else MAX_RENDERED_TRIANGLES
        )
        if triangle_budget is not None and (
            isinstance(triangle_budget, bool)
            or not isinstance(triangle_budget, int)
            or not 1 <= triangle_budget <= maximum_budget
        ):
            raise ValueError(
                "Model triangle budget must be an integer between 1 and "
                f"{maximum_budget:,} for {normalized_quality} quality"
            )
        normalized_yaw = yaw % 360.0
        normalized_pitch = min(89.0, max(-89.0, pitch))
        selected_pairs = tuple(enumerate(self.geometries))
        selected_lod = "All"
        if lod and lod.casefold() != "all":
            selected_pairs = tuple(
                (index, item) for index, item in selected_pairs
                if item.lod.casefold() == lod.casefold()
            )
            if not selected_pairs:
                raise ValueError(f"Model LOD was not found: {lod}")
            selected_lod = selected_pairs[0][1].lod
        selected_component = "All"
        if component and component.casefold() != "all":
            selected_pairs = tuple(
                (index, item) for index, item in selected_pairs
                if item.component.casefold() == component.casefold()
            )
            if not selected_pairs:
                raise ValueError(f"Model component was not found in the selected LOD: {component}")
            selected_component = selected_pairs[0][1].component
        selected = tuple(item for _index, item in selected_pairs)
        selected_materials = tuple(
            self._render_material_cache[index] for index, _item in selected_pairs
        )
        bounds_key = (selected_lod.casefold(), selected_component.casefold())
        render_bounds = self._render_bounds_cache.get(bounds_key)
        if render_bounds is None:
            render_bounds = _model_geometry_bounds(selected)
            self._render_bounds_cache[bounds_key] = render_bounds
        image, rendered = _render_model_image(
            list(selected), self.name, camera_yaw=normalized_yaw,
            camera_pitch=normalized_pitch,
            render_mode=normalized_mode, quality=normalized_quality,
            triangle_budget=triangle_budget,
            model_bounds=render_bounds,
            model_materials=selected_materials,
            interactive_triangle_cache=self._interactive_triangle_cache,
            interactive_cache_key=(
                bounds_key[0], bounds_key[1],
                triangle_budget or MODEL_RENDER_QUALITIES[normalized_quality],
            ),
        )
        metadata = dict(self.metadata)
        metadata.update(rendered)
        metadata.update({
            "model_camera_yaw": round(normalized_yaw, 2),
            "model_camera_pitch": round(normalized_pitch, 2),
            "model_camera_lod": selected_lod,
            "model_camera_component": selected_component,
        })
        return image, metadata


@dataclass(frozen=True)
class _MapEntity:
    archetype: str
    position: tuple[float, float, float]
    parent: int
    yaw: float
    scale: float


@dataclass(frozen=True)
class _NavPolygon:
    vertices: tuple[tuple[float, float, float], ...]
    flags: tuple[int, ...]


@dataclass(frozen=True)
class _NavPortal:
    position_from: tuple[float, float, float]
    position_to: tuple[float, float, float]
    portal_type: int


@dataclass(frozen=True)
class _NavPoint:
    position: tuple[float, float, float]
    angle: float
    point_type: int


@dataclass(frozen=True)
class _PathLink:
    to_area: int
    to_node: int
    flags: tuple[int, int, int]
    length: int


@dataclass(frozen=True)
class _PathNode:
    area_id: int
    node_id: int
    street: str
    position: tuple[float, float, float]
    flags: tuple[int, int, int, int, int, int]
    links: tuple[_PathLink, ...]
    vehicle: bool


@dataclass(frozen=True)
class _PathJunction:
    position: tuple[float, float, float]
    max_z: float
    size_x: int
    size_y: int


@dataclass(frozen=True)
class _ArchetypeRecord:
    name: str
    kind: str
    asset_type: str
    asset_name: str
    texture_dictionary: str
    drawable_dictionary: str
    physics_dictionary: str
    clip_dictionary: str
    lod_distance: float
    extension_count: int


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


@dataclass(frozen=True)
class NativeAssetReport:
    name: str
    suffix: str
    format_name: str
    size: int
    sha256: str
    metadata: dict[str, Any] = field(default_factory=dict)
    structured_text: str | None = None
    image_png: bytes | None = None
    warnings: tuple[str, ...] = ()
    model_scene: NativeModelScene | None = None

    def summary(self) -> str:
        lines = [
            f"Format: {self.format_name}",
            f"Size: {self.size:,} bytes",
            f"SHA-256: {self.sha256}",
        ]
        lines.extend(f"{key}: {value}" for key, value in self.metadata.items())
        if self.warnings:
            lines.extend(("", "Warnings:"))
            lines.extend(f"• {warning}" for warning in self.warnings)
        return "\n".join(lines)


def _rsc_metadata(data: bytes) -> dict[str, Any]:
    if len(data) < 16 or data[:4] not in {b"RSC7", b"RSC8"}:
        return {}
    return {
        "resource_container": data[:4].decode("ascii"),
        "resource_version": int.from_bytes(data[4:8], "little"),
        "system_flags": f"0x{int.from_bytes(data[8:12], 'little'):08X}",
        "graphics_flags": f"0x{int.from_bytes(data[12:16], 'little'):08X}",
    }


def _dds_metadata(data: bytes) -> dict[str, Any]:
    if len(data) < 128 or not data.startswith(b"DDS "):
        return {}
    height, width = struct.unpack_from("<II", data, 12)
    mipmaps = struct.unpack_from("<I", data, 28)[0]
    fourcc = data[84:88].rstrip(b"\0").decode("ascii", errors="replace")
    return {
        "dimensions": f"{width} × {height}",
        "mip_levels": mipmaps or 1,
        "pixel_format": fourcc or "uncompressed",
    }


def _gxt2_text(data: bytes) -> tuple[str | None, dict[str, Any], tuple[str, ...]]:
    if len(data) < 16 or data[:4] != b"2TXG":
        return None, {}, ()
    count = int.from_bytes(data[4:8], "little")
    try:
        entries = Gxt2Workspace.parse(data)
    except ValueError as exc:
        return None, {"label_count": count}, (str(exc),)
    lines = [f"{item['hash_hex']}  {item['text']}" for item in entries]
    return "\n".join(lines), {"label_count": len(entries)}, ()


def _format_identity(name: str, data: bytes) -> tuple[str, dict[str, Any]]:
    suffix = Path(name).suffix.casefold()
    metadata = _rsc_metadata(data)
    if data.startswith(b"RPF7"):
        return "Rockstar RPF7 archive", metadata
    if data.startswith(b"DDS "):
        metadata.update(_dds_metadata(data))
        return "DirectDraw Surface texture", metadata
    if data.startswith(b"2TXG"):
        return "Rockstar GXT2 text table", metadata
    if data[:4] in {b"ADAT", b"TADA"}:
        metadata["endianness"] = "little" if data[:4] == b"ADAT" else "big"
        if len(data) >= 12:
            metadata["awc_version"] = int.from_bytes(data[4:6], "little")
            metadata["stream_count"] = int.from_bytes(data[8:12], "little")
        return "Rockstar AWC audio container", metadata
    if data[:3] in {b"FWS", b"CWS", b"ZWS"}:
        metadata["scaleform_signature"] = data[:3].decode("ascii")
        metadata["scaleform_version"] = data[3] if len(data) > 3 else "unknown"
        return "Scaleform/SWF interface movie", metadata
    names = {
        ".rpf": "Rockstar RPF archive", ".ytd": "Rockstar texture dictionary",
        ".ydr": "Rockstar drawable", ".ydd": "Rockstar drawable dictionary",
        ".yft": "Rockstar fragment", ".ybn": "Rockstar collision bounds",
        ".ymap": "Rockstar map placement", ".ytyp": "Rockstar archetype definition",
        ".ymt": "Rockstar metadata resource", ".ymf": "Rockstar metadata manifest",
        ".pso": "Rockstar PSO metadata resource",
        ".gxt2": "Rockstar GXT2 text table",
        ".awc": "Rockstar AWC audio container", ".rel": "Rockstar audio relationship",
        ".gfx": "Scaleform interface movie", ".ycd": "Rockstar clip dictionary",
        ".ynd": "Rockstar path nodes", ".ynv": "Rockstar navigation mesh",
        ".ypt": "Rockstar particle dictionary",
    }
    return names.get(suffix, "Binary asset"), metadata


def _texture_contact_sheet(folder: Path) -> tuple[bytes | None, int]:
    candidates = sorted(
        (path for path in folder.rglob("*") if path.suffix.casefold() in {
            ".dds", ".png", ".jpg", ".jpeg", ".bmp",
        }),
        key=lambda path: path.name.casefold(),
    )
    thumbnails: list[tuple[Image.Image, str]] = []
    for path in candidates[:24]:
        try:
            with Image.open(path) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGBA")
                image.thumbnail((190, 150), Image.Resampling.LANCZOS)
                thumbnails.append((image.copy(), path.stem))
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            continue
    if not thumbnails:
        return None, len(candidates)
    columns = 4
    cell_width, cell_height = 210, 185
    rows = (len(thumbnails) + columns - 1) // columns
    sheet = Image.new("RGBA", (columns * cell_width, rows * cell_height), "#18201d")
    draw = ImageDraw.Draw(sheet)
    for index, (image, label) in enumerate(thumbnails):
        left = (index % columns) * cell_width
        top = (index // columns) * cell_height
        x = left + ((cell_width - image.width) // 2)
        y = top + 5 + ((150 - image.height) // 2)
        sheet.alpha_composite(image, (x, y))
        draw.text((left + 8, top + 160), label[:30], fill="#E8F2EC")
    output = io.BytesIO()
    sheet.convert("RGB").save(output, format="PNG")
    return output.getvalue(), len(candidates)


def _local_name(element: etree._Element) -> str:
    return etree.QName(element).localname


def _model_position_offset(layout: etree._Element | None) -> int | None:
    """Return the Position offset for known CodeWalker vertex semantics.

    CodeWalker emits one vertex per line. We still refuse to guess when an
    unknown semantic precedes Position so a malformed/custom layout cannot be
    rendered as convincing but incorrect geometry.
    """
    if layout is None:
        return None
    offset = 0
    for semantic in layout:
        if not isinstance(semantic.tag, str):
            continue
        name = _local_name(semantic)
        if name.casefold().startswith("position"):
            return offset
        folded = name.casefold()
        if folded.startswith("texcoord"):
            width = 2
        elif folded.startswith("colour") or folded.startswith("color"):
            width = 4
        elif folded.startswith("blendweights") or folded.startswith("blendindices"):
            width = 4
        elif folded.startswith("normal") or folded.startswith("binormal"):
            width = 3
        elif folded.startswith("tangent"):
            width = 4
        else:
            return None
        offset += width
    return None


def _model_texcoord_offset(layout: etree._Element | None) -> int | None:
    """Return the first UV-set offset for a fully understood vertex layout."""
    if layout is None:
        return None
    offset = 0
    for semantic in layout:
        if not isinstance(semantic.tag, str):
            continue
        folded = _local_name(semantic).casefold()
        if folded == "texcoord0" or folded == "texcoord":
            return offset
        if folded.startswith("position") or folded.startswith("normal") \
                or folded.startswith("binormal"):
            width = 3
        elif folded.startswith("texcoord"):
            width = 2
        elif folded.startswith("colour") or folded.startswith("color") \
                or folded.startswith("blendweights") \
                or folded.startswith("blendindices") \
                or folded.startswith("tangent"):
            width = 4
        else:
            return None
        offset += width
    return None


def _model_lod(vertex_buffer: etree._Element) -> str:
    parent = vertex_buffer.getparent()
    while parent is not None:
        name = _local_name(parent)
        if name.startswith("DrawableModels"):
            return name.removeprefix("DrawableModels") or "Unknown"
        parent = parent.getparent()
    return "Default"


def _direct_model_text(parent: etree._Element, name: str) -> str:
    for child in parent:
        if isinstance(child.tag, str) and _local_name(child) == name:
            return (child.text or child.get("value", "")).strip()
    return ""


_ModelMatrix = tuple[
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
    tuple[float, float, float, float],
]
_IDENTITY_MODEL_MATRIX: _ModelMatrix = (
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
)
_RIGHT_WHEEL_MODEL_MATRIX: _ModelMatrix = (
    (-1.0, 0.0, 0.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, -1.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
)


def _multiply_model_matrices(
    left: _ModelMatrix, right: _ModelMatrix,
) -> _ModelMatrix:
    return tuple(
        tuple(sum(left[row][slot] * right[slot][column] for slot in range(4))
              for column in range(4))
        for row in range(4)
    )  # type: ignore[return-value]


def _model_trs_matrix(bone: etree._Element) -> _ModelMatrix:
    """Return one CodeWalker skeleton bone's local column-vector transform."""
    translation = next((
        item for item in bone
        if isinstance(item.tag, str) and _local_name(item) == "Translation"
    ), None)
    rotation = next((
        item for item in bone
        if isinstance(item.tag, str) and _local_name(item) == "Rotation"
    ), None)
    scale = next((
        item for item in bone
        if isinstance(item.tag, str) and _local_name(item) == "Scale"
    ), None)
    try:
        tx, ty, tz = tuple(
            float(translation.get(axis, "0")) if translation is not None else 0.0
            for axis in ("x", "y", "z")
        )
        qx, qy, qz, qw = tuple(
            float(rotation.get(axis, "0" if axis != "w" else "1"))
            if rotation is not None else (1.0 if axis == "w" else 0.0)
            for axis in ("x", "y", "z", "w")
        )
        sx, sy, sz = tuple(
            float(scale.get(axis, "1")) if scale is not None else 1.0
            for axis in ("x", "y", "z")
        )
    except ValueError as exc:
        raise ValueError("A fragment skeleton bone has a non-numeric transform") from exc
    values = (tx, ty, tz, qx, qy, qz, qw, sx, sy, sz)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("A fragment skeleton bone has a non-finite transform")
    magnitude = math.sqrt((qx * qx) + (qy * qy) + (qz * qz) + (qw * qw))
    if magnitude <= 1e-12:
        raise ValueError("A fragment skeleton bone has a zero quaternion")
    qx, qy, qz, qw = (
        qx / magnitude, qy / magnitude, qz / magnitude, qw / magnitude,
    )
    xx, yy, zz = qx * qx, qy * qy, qz * qz
    xy, xz, yz = qx * qy, qx * qz, qy * qz
    xw, yw, zw = qx * qw, qy * qw, qz * qw
    return (
        ((1.0 - (2.0 * (yy + zz))) * sx, (2.0 * (xy - zw)) * sy,
         (2.0 * (xz + yw)) * sz, tx),
        ((2.0 * (xy + zw)) * sx, (1.0 - (2.0 * (xx + zz))) * sy,
         (2.0 * (yz - xw)) * sz, ty),
        ((2.0 * (xz - yw)) * sx, (2.0 * (yz + xw)) * sy,
         (1.0 - (2.0 * (xx + yy))) * sz, tz),
        (0.0, 0.0, 0.0, 1.0),
    )


def _model_drawable_matrix(drawable: etree._Element) -> _ModelMatrix:
    """Convert CodeWalker's four row-vector 3D rows into a 4x4 matrix."""
    matrix = next((
        item for item in drawable
        if isinstance(item.tag, str) and _local_name(item) == "Matrix"
    ), None)
    if matrix is None or not (matrix.text or "").strip():
        return _IDENTITY_MODEL_MATRIX
    try:
        rows = tuple(
            tuple(float(value) for value in line.split())
            for line in (matrix.text or "").splitlines() if line.split()
        )
    except ValueError as exc:
        raise ValueError("A fragment child drawable has a non-numeric matrix") from exc
    if len(rows) != 4 or any(len(row) != 3 for row in rows):
        raise ValueError("A fragment child drawable matrix must contain four 3D rows")
    if not all(math.isfinite(value) for row in rows for value in row):
        raise ValueError("A fragment child drawable has a non-finite matrix")
    return (
        (rows[0][0], rows[1][0], rows[2][0], rows[3][0]),
        (rows[0][1], rows[1][1], rows[2][1], rows[3][1]),
        (rows[0][2], rows[1][2], rows[2][2], rows[3][2]),
        (0.0, 0.0, 0.0, 1.0),
    )


def _transform_model_geometry(
    geometry: _ModelGeometry, matrix: _ModelMatrix, component: str, *,
    reverse_winding: bool = False,
) -> _ModelGeometry:
    vertices = tuple(
        (
            (matrix[0][0] * x) + (matrix[0][1] * y)
            + (matrix[0][2] * z) + matrix[0][3],
            (matrix[1][0] * x) + (matrix[1][1] * y)
            + (matrix[1][2] * z) + matrix[1][3],
            (matrix[2][0] * x) + (matrix[2][1] * y)
            + (matrix[2][2] * z) + matrix[2][3],
        )
        for x, y, z in geometry.vertices
    )
    triangles = (
        tuple((first, third, second) for first, second, third in geometry.triangles)
        if reverse_winding else geometry.triangles
    )
    return _ModelGeometry(
        vertices=vertices, triangles=triangles, lod=geometry.lod,
        component=component, material_index=geometry.material_index,
        material_name=geometry.material_name, texture_names=geometry.texture_names,
        texcoords=geometry.texcoords,
        texture_parameters=geometry.texture_parameters,
    )


@dataclass(frozen=True)
class _FragmentGeometryPlacement:
    matrix: _ModelMatrix
    component: str
    mirrored_matrix: _ModelMatrix | None = None
    mirrored_component: str = ""


def _fragment_child_geometry_placements(
    root: etree._Element,
) -> dict[etree._Element, _FragmentGeometryPlacement]:
    """Resolve local fragment-child drawables into the skeleton bind pose.

    Vehicle fragments commonly store only their left front/rear wheel drawables;
    the game mirrors those templates onto the right-side wheel bones. The XML
    vertex rows remain local to the child, so a geometry-only renderer must do
    both assembly steps explicitly.
    """
    if _local_name(root) != "Fragment":
        return {}
    bone_nodes = root.xpath(
        "./*[local-name()='Drawable']/*[local-name()='Skeleton']"
        "/*[local-name()='Bones']/*[local-name()='Item']"
    )
    if not bone_nodes:
        return {}
    by_index: dict[int, etree._Element] = {}
    by_tag: dict[int, etree._Element] = {}
    by_name: dict[str, etree._Element] = {}
    parents: dict[int, int] = {}
    for bone in bone_nodes:
        try:
            index = int(_direct_model_text(bone, "Index"), 10)
            tag = int(_direct_model_text(bone, "Tag"), 10)
            parent = int(_direct_model_text(bone, "ParentIndex"), 10)
        except ValueError as exc:
            raise ValueError("A fragment skeleton bone index is invalid") from exc
        name = _direct_model_text(bone, "Name")
        by_index[index] = bone
        by_tag[tag] = bone
        parents[index] = parent
        if name:
            by_name[name.casefold()] = bone

    world_cache: dict[int, _ModelMatrix] = {}
    resolving: set[int] = set()

    def world_matrix(bone: etree._Element) -> _ModelMatrix:
        index = int(_direct_model_text(bone, "Index"), 10)
        cached = world_cache.get(index)
        if cached is not None:
            return cached
        if index in resolving:
            raise ValueError("A fragment skeleton contains a bone-parent cycle")
        resolving.add(index)
        local = _model_trs_matrix(bone)
        parent_index = parents[index]
        result = (
            _multiply_model_matrices(world_matrix(by_index[parent_index]), local)
            if parent_index >= 0 and parent_index in by_index else local
        )
        resolving.remove(index)
        world_cache[index] = result
        return result

    child_drawables = root.xpath(
        "./*[local-name()='Physics']/*[local-name()='LOD1']"
        "/*[local-name()='Children']/*[local-name()='Item']"
        "/*[local-name()='Drawable']"
    )
    attached_tags = {
        int(_direct_model_text(drawable.getparent(), "BoneTag"), 10)
        for drawable in child_drawables
        if drawable.xpath(".//*[local-name()='VertexBuffer']")
        and _direct_model_text(drawable.getparent(), "BoneTag")
    }
    placements: dict[etree._Element, _FragmentGeometryPlacement] = {}
    wheel_pairs = {"wheel_lf": "wheel_rf", "wheel_lr": "wheel_rr"}
    for drawable in child_drawables:
        owner = drawable.getparent()
        raw_tag = "" if owner is None else _direct_model_text(owner, "BoneTag")
        if not raw_tag:
            continue
        try:
            tag = int(raw_tag, 10)
        except ValueError as exc:
            raise ValueError("A fragment child has an invalid BoneTag") from exc
        bone = by_tag.get(tag)
        if bone is None:
            continue
        bone_name = _direct_model_text(bone, "Name") or f"Bone {tag}"
        folded_bone_name = bone_name.casefold()
        if folded_bone_name not in {*wheel_pairs, *wheel_pairs.values()}:
            continue
        drawable_matrix = _model_drawable_matrix(drawable)
        matrix = _multiply_model_matrices(world_matrix(bone), drawable_matrix)
        mirrored_matrix: _ModelMatrix | None = None
        mirrored_component = ""
        target_name = wheel_pairs.get(folded_bone_name)
        target_bone = by_name.get(target_name or "")
        if target_bone is not None:
            target_tag = int(_direct_model_text(target_bone, "Tag"), 10)
            if target_tag not in attached_tags:
                mirrored_matrix = _multiply_model_matrices(
                    world_matrix(target_bone),
                    _multiply_model_matrices(
                        _RIGHT_WHEEL_MODEL_MATRIX, drawable_matrix,
                    ),
                )
                mirrored_component = _direct_model_text(target_bone, "Name")
        placement = _FragmentGeometryPlacement(
            matrix, bone_name, mirrored_matrix, mirrored_component,
        )
        for vertex_buffer in drawable.xpath(".//*[local-name()='VertexBuffer']"):
            placements[vertex_buffer] = placement
    return placements


def _model_materials(root: etree._Element) -> tuple[NativeModelMaterial, ...]:
    materials: list[NativeModelMaterial] = []
    shaders = root.xpath(
        ".//*[local-name()='ShaderGroup']/*[local-name()='Shaders']/*[local-name()='Item']"
    )
    for index, shader in enumerate(shaders):
        materials.append(_model_material_record(shader, index))
    return tuple(materials)


def _model_material_record(
    shader: etree._Element, index: int,
) -> NativeModelMaterial:
    parameters = tuple(
        (parameter.get("name", "").strip(), texture_name)
        for parameter in shader.xpath(
            "./*[local-name()='Parameters']/*[local-name()='Item']"
        )
        if parameter.get("type", "").casefold() == "texture"
        for texture_name in (_direct_model_text(parameter, "Name"),)
        if texture_name
    )
    textures = tuple(dict.fromkeys(value for _slot, value in parameters))
    return NativeModelMaterial(
        index=index,
        name=resolve_shader_name(
            _direct_model_text(shader, "Name") or f"Shader {index}"
        ),
        texture_names=textures,
        texture_parameters=parameters,
    )


def _model_component_name(vertex_buffer: etree._Element, ordinal: int) -> str:
    geometry = vertex_buffer.getparent()
    geometries = None if geometry is None else geometry.getparent()
    drawable = None if geometries is None else geometries.getparent()
    if drawable is None:
        return f"{_model_lod(vertex_buffer)} drawable {ordinal + 1}"
    authored_name = _direct_model_text(drawable, "Name")
    if authored_name:
        return authored_name
    container = drawable.getparent()
    siblings = (
        [item for item in container if isinstance(item.tag, str) and _local_name(item) == "Item"]
        if container is not None else []
    )
    drawable_index = siblings.index(drawable) + 1 if drawable in siblings else ordinal + 1
    return f"{_model_lod(vertex_buffer)} drawable {drawable_index}"


def _model_geometry_material(
    vertex_buffer: etree._Element,
    materials: tuple[NativeModelMaterial, ...],
) -> NativeModelMaterial | None:
    geometry = vertex_buffer.getparent()
    if geometry is None:
        return None
    raw_index = _direct_model_text(geometry, "ShaderIndex")
    if not raw_index:
        return None
    try:
        index = int(raw_index, 10)
    except ValueError as exc:
        raise ValueError("A model geometry has a non-integer ShaderIndex") from exc
    ancestor = geometry
    root = geometry
    while ancestor is not None:
        root = ancestor
        shaders = ancestor.xpath(
            "./*[local-name()='ShaderGroup']/*[local-name()='Shaders']/*[local-name()='Item']"
        )
        if shaders:
            if index < 0 or index >= len(shaders):
                return None
            return _model_material_record(shaders[index], index)
        ancestor = ancestor.getparent()
    # Fragment physics children share the primary Drawable's ShaderGroup, but
    # that drawable is a sibling rather than an XML ancestor. The root material
    # catalog is therefore the authoritative index table for those children.
    if _local_name(root) == "Fragment" and 0 <= index < len(materials):
        return materials[index]
    return None


def _read_model_geometry(
    vertex_buffer: etree._Element, *, ordinal: int = 0,
    materials: tuple[NativeModelMaterial, ...] = (),
) -> _ModelGeometry | None:
    layout = vertex_buffer.find("./Layout")
    data = vertex_buffer.find("./Data")
    offset = _model_position_offset(layout)
    texcoord_offset = _model_texcoord_offset(layout)
    if data is None or not data.text or offset is None:
        return None
    vertices: list[tuple[float, float, float]] = []
    texcoords: list[tuple[float, float]] = []
    for line in data.text.splitlines():
        fields = line.split()
        if not fields:
            continue
        if len(fields) < offset + 3:
            raise ValueError("A model vertex row is shorter than its declared layout")
        try:
            point = tuple(float(value) for value in fields[offset:offset + 3])
        except ValueError as exc:
            raise ValueError("A model vertex contains a non-numeric position") from exc
        if not all(math.isfinite(value) for value in point):
            raise ValueError("A model vertex contains a non-finite position")
        vertices.append((point[0], point[1], point[2]))
        if texcoord_offset is not None:
            if len(fields) < texcoord_offset + 2:
                raise ValueError("A model vertex row is shorter than its UV layout")
            try:
                uv = tuple(float(value) for value in fields[texcoord_offset:texcoord_offset + 2])
            except ValueError as exc:
                raise ValueError("A model UV contains a non-numeric value") from exc
            if not all(math.isfinite(value) for value in uv):
                raise ValueError("A model UV contains a non-finite value")
            texcoords.append((uv[0], uv[1]))
        if len(vertices) > MAX_MODEL_VERTICES:
            raise ValueError("Model preview exceeds the guarded vertex limit")
    if not vertices:
        return None

    geometry = vertex_buffer.getparent()
    material = _model_geometry_material(vertex_buffer, materials)
    component = _model_component_name(vertex_buffer, ordinal)
    geometry_details = {
        "component": component,
        "material_index": material.index if material is not None else None,
        "material_name": material.name if material is not None else "",
        "texture_names": material.texture_names if material is not None else (),
        "texcoords": tuple(texcoords),
        "texture_parameters": (
            material.texture_parameters if material is not None else ()
        ),
    }
    index_data = None if geometry is None else geometry.find("./IndexBuffer/Data")
    if index_data is None or not index_data.text:
        return _ModelGeometry(
            tuple(vertices), (), _model_lod(vertex_buffer), **geometry_details,
        )
    indices: list[int] = []
    triangles: list[tuple[int, int, int]] = []
    for line in index_data.text.splitlines():
        for token in line.split():
            try:
                indices.append(int(token, 10))
            except ValueError as exc:
                raise ValueError("A model index buffer contains a non-integer value") from exc
            if len(indices) == 3:
                triangle = (indices[0], indices[1], indices[2])
                if any(index < 0 or index >= len(vertices) for index in triangle):
                    raise ValueError("A model triangle references a missing vertex")
                triangles.append(triangle)
                indices.clear()
                if len(triangles) > MAX_MODEL_TRIANGLES:
                    raise ValueError("Model preview exceeds the guarded triangle limit")
    if indices:
        raise ValueError("A model index buffer does not contain complete triangles")
    return _ModelGeometry(
        tuple(vertices), tuple(triangles), _model_lod(vertex_buffer),
        **geometry_details,
    )


def _model_drawable_count(root: etree._Element) -> int:
    name = _local_name(root)
    if name == "DrawableDictionary":
        return len(root.xpath("./*[local-name()='Item']"))
    if name in {"Drawable", "Fragment"}:
        return 1
    count = len(root.xpath(".//*[local-name()='Drawable']"))
    return count or 1


def _model_resource_metadata(root: etree._Element) -> dict[str, Any]:
    shader_items = root.xpath(
        ".//*[local-name()='ShaderGroup']/*[local-name()='Shaders']/*[local-name()='Item']"
    )
    shader_names: list[str] = []
    texture_slots = 0
    texture_names: set[str] = set()
    for shader in shader_items:
        name_element = next((
            child for child in shader
            if isinstance(child.tag, str) and _local_name(child) == "Name"
        ), None)
        name = ((name_element.text or "").strip() if name_element is not None else "")
        if name:
            shader_names.append(name)
        for parameter in shader.xpath(
            "./*[local-name()='Parameters']/*[local-name()='Item']"
        ):
            if parameter.get("type", "").casefold() != "texture":
                continue
            texture_slots += 1
            texture = next((
                child for child in parameter
                if isinstance(child.tag, str) and _local_name(child) == "Name"
            ), None)
            texture_name = ((texture.text or "").strip() if texture is not None else "")
            if texture_name:
                texture_names.add(texture_name)
    skinned_models = 0
    for has_skin in root.xpath(".//*[local-name()='HasSkin']"):
        try:
            skinned_models += int(has_skin.get("value", "0"), 10) != 0
        except ValueError as exc:
            raise ValueError("Model HasSkin value is non-integer") from exc
    bound_bones: set[int] = set()
    for bone_ids in root.xpath(".//*[local-name()='BoneIDs']"):
        bound_bones.update(_raw_integer_values(bone_ids, context="Model BoneIDs"))
    skeleton_bones = len(root.xpath(
        ".//*[local-name()='Skeleton']/*[local-name()='Bones']/*[local-name()='Item']"
    ))
    light_count = len(root.xpath(
        ".//*[local-name()='Lights']/*[local-name()='Item']"
    ))
    metadata: dict[str, Any] = {
        "model_shader_count": len(shader_items),
        "model_texture_parameter_count": texture_slots,
        "model_texture_reference_count": len(texture_names),
        "model_skinned_models": skinned_models,
        "model_bone_binding_count": len(bound_bones),
        "model_skeleton_bones": skeleton_bones,
        "model_light_count": light_count,
    }
    if shader_names:
        metadata["model_shader_names"] = ", ".join(shader_names[:16])
        if len(shader_names) > 16:
            metadata["model_shader_names"] += f", … (+{len(shader_names) - 16})"
    if texture_names:
        ordered = sorted(texture_names, key=str.casefold)
        metadata["model_texture_names"] = ", ".join(ordered[:16])
        if len(ordered) > 16:
            metadata["model_texture_names"] += f", … (+{len(ordered) - 16})"
    return metadata


def _project_model_point(
    point: tuple[float, float, float],
    center: tuple[float, float, float],
    *, yaw_degrees: float = 34.0, pitch_degrees: float = 24.0,
) -> tuple[float, float, float]:
    x, y, z = (point[index] - center[index] for index in range(3))
    yaw = math.radians(yaw_degrees)
    pitch = math.radians(pitch_degrees)
    rotated_x = (x * math.cos(yaw)) - (y * math.sin(yaw))
    rotated_y = (x * math.sin(yaw)) + (y * math.cos(yaw))
    screen_y = (z * math.cos(pitch)) - (rotated_y * math.sin(pitch))
    depth = (rotated_y * math.cos(pitch)) + (z * math.sin(pitch))
    return rotated_x, screen_y, depth


_SEMANTIC_MODEL_COLORS: tuple[
    tuple[str, tuple[str, ...], tuple[int, int, int]], ...
] = (
    ("glass", ("glass", "window", "windscreen", "windshield"), (78, 114, 134)),
    ("light", ("emissive", "headlight", "taillight", "lamp", "light"), (188, 170, 102)),
    ("tyre", ("tyre", "tire", "rubber"), (45, 48, 48)),
    ("chrome", ("chrome", "mirror"), (165, 168, 168)),
    ("brake", ("brake", "caliper"), (138, 58, 50)),
    ("interior", (
        "interior", "dashboard", "dash", "seat", "leather", "carpet",
        "speaker", "stitch", "dial", "screen",
    ), (62, 59, 56)),
    ("paint", ("paint", "carpaint", "bodywork", "body"), (45, 132, 77)),
    ("wheel", ("wheel", "alloy", "rim"), (100, 105, 107)),
    ("plastic", ("plastic", "trim", "carbon"), (73, 78, 76)),
    ("metal", (
        "metal", "steel", "aluminium", "aluminum", "engine", "undercarriage",
    ), (126, 130, 131)),
    ("decal", ("decal", "badge", "sign", "label"), (145, 143, 134)),
)

# CodeWalker may preserve a shader as its Jenkins hash when an authored package
# does not carry a readable name. These common vehicle hashes are stable GTA
# shader identities, not package-specific guesses. Texture semantics are checked
# first because some authors reuse a shader for a materially different surface.
_HASHED_VEHICLE_SHADER_SEMANTICS = {
    "hash_f9fb7331": "paint",      # vehicle_paint1
    "hash_1d5f09ce": "tyre",       # vehicle_tire
    "hash_ffe6fbea": "decal",      # vehicle_badges
    "hash_8a7a2bef": "decal",      # vehicle_licenseplate
    "hash_2a92aee4": "interior",   # vehicle_interior2
    "hash_7c98d207": "glass",      # vehicle_vehglass
    "hash_c9866cc2": "glass",      # vehicle_vehglass_inner
    "hash_e515a6e7": "light",      # vehicle_lightsemissive
    "hash_0f8bd089": "light",      # vehicle_dash_emissive
}


def _semantic_model_color(
    semantic: str,
) -> tuple[int, int, int] | None:
    return next((
        color for name, _tokens, color in _SEMANTIC_MODEL_COLORS
        if name == semantic
    ), None)


def _model_material_identity(
    geometry: _ModelGeometry,
) -> tuple[str, str, tuple[int, int, int]]:
    """Return a stable label, semantic class, and diagnostic base colour."""
    if geometry.material_name:
        identity = geometry.material_name
    elif geometry.material_index is not None:
        identity = f"Material {geometry.material_index}"
    else:
        identity = f"Unbound · {geometry.component}"
    # `vehicle_generic_glassdirt` is a common auxiliary texture on lights and
    # must not turn every emissive surface blue.
    texture_searchable = " ".join(geometry.texture_names).casefold().replace(
        "glassdirt", ""
    )
    for semantic, tokens, color in _SEMANTIC_MODEL_COLORS:
        if any(token in texture_searchable for token in tokens):
            return identity, semantic, color
    shader_semantic = _HASHED_VEHICLE_SHADER_SEMANTICS.get(identity.casefold())
    if shader_semantic:
        shader_color = _semantic_model_color(shader_semantic)
        if shader_color is not None:
            return identity, shader_semantic, shader_color
    identity_searchable = identity.casefold()
    for semantic, tokens, color in _SEMANTIC_MODEL_COLORS:
        if any(token in identity_searchable for token in tokens):
            return identity, semantic, color

    # Unknown shaders should still read as parts of one object in Shaded mode.
    # Keep them in a narrow graphite/slate range; the tiny deterministic shift
    # separates overlapping pieces without producing a material-ID heatmap.
    digest = hashlib.sha256(identity.casefold().encode("utf-8")).digest()
    level = 96 + (digest[4] % 16)
    warm_shift = (digest[5] % 7) - 3
    cool_shift = (digest[6] % 9) - 4
    return identity, "neutral fallback", (
        level + warm_shift,
        level + 2,
        level + cool_shift,
    )


def _material_mode_color(
    color: tuple[int, int, int], identity: str = "",
) -> tuple[int, int, int]:
    """Return a repeatable high-contrast colour for a material-ID pass."""
    if identity:
        digest = hashlib.sha256(identity.casefold().encode("utf-8")).digest()
        hue = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
        saturation = 0.66 + (digest[4] / 255.0) * 0.18
        value = 0.80 + (digest[5] / 255.0) * 0.15
    else:
        red, green, blue = (value / 255.0 for value in color)
        hue, saturation, value = colorsys.rgb_to_hsv(red, green, blue)
        saturation = max(0.68, saturation)
        value = max(0.82, value)
    converted = colorsys.hsv_to_rgb(hue, saturation, value)
    return tuple(round(channel * 255) for channel in converted)


def _shade_color(
    color: tuple[int, int, int], intensity: float,
) -> tuple[int, int, int]:
    # This runs once per shaded triangle. Spell out the fixed RGB channels to
    # avoid allocating a generator and dispatching min/max for every channel.
    return (
        min(255, max(0, round(color[0] * intensity))),
        min(255, max(0, round(color[1] * intensity))),
        min(255, max(0, round(color[2] * intensity))),
    )


def _uniform_sample_indices(total: int, budget: int) -> tuple[int, ...] | None:
    """Select an ordered, deterministic, globally distributed bounded sample."""
    if total <= budget:
        return None
    # `total > budget` makes the integer sequence strictly increasing, so a
    # tuple is both smaller than a set and can be consumed without walking every
    # omitted source item. That distinction is substantial for 500k-face models.
    return tuple((slot * total) // budget for slot in range(budget))


def _uniform_triangle_indices(total: int, budget: int) -> tuple[int, ...] | None:
    """Select a deterministic, globally distributed bounded triangle sample."""
    return _uniform_sample_indices(total, budget)


@lru_cache(maxsize=1)
def _model_render_background() -> Image.Image:
    """Build the immutable diagnostic viewport chrome shared by model frames."""
    width, height = 960, 680
    view_left, view_top, view_right, view_bottom = 38, 76, width - 38, height - 56
    image = Image.new("RGB", (width, height), "#101714")
    draw = ImageDraw.Draw(image)
    vertical_span = max(1, view_bottom - view_top)
    for y in range(view_top, view_bottom + 1):
        ratio = (y - view_top) / vertical_span
        color = tuple(round(start + ((end - start) * ratio)) for start, end in zip(
            (19, 29, 24), (10, 15, 13),
        ))
        draw.line((view_left, y, view_right, y), fill=color)
    for y in range(view_top, view_bottom + 1, 48):
        draw.line((view_left, y, view_right, y), fill="#18231e", width=1)
    for x in range(view_left, view_right + 1, 48):
        draw.line((x, view_top, x, view_bottom), fill="#18231e", width=1)
    draw.rectangle((view_left, view_top, view_right, view_bottom), outline="#2d4036")
    return image


def _convex_hull(
    points: list[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    """Return a deterministic monotonic-chain hull for a preview underlay."""
    ordered = sorted(set(points))
    if len(ordered) <= 2:
        return tuple(ordered)

    def cross(
        origin: tuple[float, float], first: tuple[float, float],
        second: tuple[float, float],
    ) -> float:
        return (
            (first[0] - origin[0]) * (second[1] - origin[1])
            - (first[1] - origin[1]) * (second[0] - origin[0])
        )

    lower: list[tuple[float, float]] = []
    for point in ordered:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(ordered):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return tuple(lower[:-1] + upper[:-1])


def _render_model_image(
    geometries: list[_ModelGeometry], name: str, *,
    title: str = "MODEL PREVIEW", geometry_label: str = "geometries",
    camera_yaw: float = 34.0, camera_pitch: float = 24.0,
    render_mode: str = "shaded", quality: str = "final",
    triangle_budget: int | None = None,
    model_bounds: _ModelRenderBounds | None = None,
    model_materials: tuple[
        tuple[str, str, tuple[int, int, int]], ...
    ] | None = None,
    interactive_triangle_cache: dict[
        tuple[str, str, int], tuple[_PreparedInteractiveTriangle, ...]
    ] | None = None,
    interactive_cache_key: tuple[str, str, int] | None = None,
) -> tuple[Image.Image, dict[str, Any]]:
    normalized_mode = render_mode.strip().casefold()
    if normalized_mode not in MODEL_RENDER_MODES:
        choices = ", ".join(sorted(MODEL_RENDER_MODES))
        raise ValueError(f"Model render mode must be one of: {choices}")
    normalized_quality = quality.strip().casefold()
    if normalized_quality not in MODEL_RENDER_QUALITIES:
        choices = ", ".join(MODEL_RENDER_QUALITIES)
        raise ValueError(f"Model render quality must be one of: {choices}")
    maximum_budget = (
        MAX_MODEL_TRIANGLES
        if normalized_quality == "full"
        else MAX_RENDERED_TRIANGLES
    )
    if triangle_budget is not None and (
        isinstance(triangle_budget, bool)
        or not isinstance(triangle_budget, int)
        or not 1 <= triangle_budget <= maximum_budget
    ):
        raise ValueError(
            "Model triangle budget must be an integer between 1 and "
            f"{maximum_budget:,} for {normalized_quality} quality"
        )
    configured_budget = (
        triangle_budget
        if triangle_budget is not None
        else MODEL_RENDER_QUALITIES[normalized_quality]
    )
    supplied_bounds = model_bounds is not None
    minima, maxima, vertex_count = (
        model_bounds if model_bounds is not None else _model_geometry_bounds(geometries)
    )
    center = tuple((minima[axis] + maxima[axis]) / 2.0 for axis in range(3))
    center_0, center_1, center_2 = center
    yaw_radians = math.radians(camera_yaw)
    pitch_radians = math.radians(camera_pitch)
    yaw_cos = math.cos(yaw_radians)
    yaw_sin = math.sin(yaw_radians)
    pitch_cos = math.cos(pitch_radians)
    pitch_sin = math.sin(pitch_radians)

    def project(point: tuple[float, float, float]) -> tuple[float, float, float]:
        """Project with camera trigonometry prepared once for this frame."""
        x = point[0] - center_0
        y = point[1] - center_1
        z = point[2] - center_2
        rotated_x = (x * yaw_cos) - (y * yaw_sin)
        rotated_y = (x * yaw_sin) + (y * yaw_cos)
        return (
            rotated_x,
            (z * pitch_cos) - (rotated_y * pitch_sin),
            (rotated_y * pitch_cos) + (z * pitch_sin),
        )

    min_x = min_y = math.inf
    max_x = max_y = -math.inf
    # Orthographic projection is linear, so the eight AABB corners provide a
    # conservative screen extent. This replaces an O(all vertices) pass on every
    # camera update while retaining stable, selection-specific framing.
    for x in (minima[0], maxima[0]):
        for y in (minima[1], maxima[1]):
            for z in (minima[2], maxima[2]):
                px, py, _depth = project((x, y, z))
                min_x, max_x = min(min_x, px), max(max_x, px)
                min_y, max_y = min(min_y, py), max(max_y, py)
    width, height = 960, 680
    view_left, view_top, view_right, view_bottom = 38, 76, width - 38, height - 56
    span_x = max(max_x - min_x, 1e-9)
    span_y = max(max_y - min_y, 1e-9)
    scale = min((view_right - view_left) / span_x, (view_bottom - view_top) / span_y)
    center_x = (view_left + view_right) / 2.0
    center_y = (view_top + view_bottom) / 2.0

    def screen(point: tuple[float, float, float]) -> tuple[float, float, float]:
        x = point[0] - center_0
        y = point[1] - center_1
        z = point[2] - center_2
        px = (x * yaw_cos) - (y * yaw_sin)
        rotated_y = (x * yaw_sin) + (y * yaw_cos)
        py = (z * pitch_cos) - (rotated_y * pitch_sin)
        depth = (rotated_y * pitch_cos) + (z * pitch_sin)
        return center_x + (px * scale), center_y - (py * scale), depth

    total_triangles = sum(len(geometry.triangles) for geometry in geometries)
    selected_indices = _uniform_triangle_indices(total_triangles, configured_budget)
    # depth, stable ordinal, geometry, local indices, screen polygon, light.
    # Material records are held once per geometry instead of once per triangle;
    # this matters for opt-in Full renders near the one-million-face scene cap.
    rendered: list[
        tuple[
            float, int, int, tuple[int, int, int],
            tuple[tuple[float, float], ...], float,
        ]
    ] = []
    geometry_render_materials: list[
        tuple[str, str, tuple[int, int, int]]
    ] = []
    prepared_materials = (
        model_materials
        if model_materials is not None and len(model_materials) == len(geometries)
        else tuple(_model_material_identity(geometry) for geometry in geometries)
    )
    directional = (0.32, -0.42, 0.85)
    directional_length = math.sqrt(sum(value * value for value in directional))
    directional = tuple(value / directional_length for value in directional)
    for geometry_index, geometry in enumerate(geometries):
        identity, semantic, base_color = prepared_materials[geometry_index]
        if normalized_mode == "materials":
            material_key = "|".join((
                str(geometry.material_index), identity, *geometry.texture_names,
            ))
            base_color = _material_mode_color(base_color, material_key)
        geometry_render_materials.append((identity, semantic, base_color))

    cached_triangles = None
    use_interactive_cache = (
        normalized_quality == "interactive"
        and selected_indices is not None
        and interactive_triangle_cache is not None
        and interactive_cache_key is not None
    )
    if use_interactive_cache:
        cached_triangles = interactive_triangle_cache.get(interactive_cache_key)

    if cached_triangles is not None:
        for (
            ordinal, geometry_index, triangle, raw_0, raw_1, raw_2, light,
        ) in cached_triangles:
            transformed_0 = screen(raw_0)
            transformed_1 = screen(raw_1)
            transformed_2 = screen(raw_2)
            rendered.append((
                (transformed_0[2] + transformed_1[2] + transformed_2[2]) / 3.0,
                ordinal,
                geometry_index,
                triangle,
                (
                    (transformed_0[0], transformed_0[1]),
                    (transformed_1[0], transformed_1[1]),
                    (transformed_2[0], transformed_2[1]),
                ),
                light,
            ))
    else:
        prepared_triangles: list[_PreparedInteractiveTriangle] = []
        global_triangle = 0
        selection_cursor = 0
        for geometry_index, geometry in enumerate(geometries):
            geometry_start = global_triangle
            geometry_end = geometry_start + len(geometry.triangles)
            if selected_indices is None:
                triangle_items = (
                    (geometry_start + local_index, triangle)
                    for local_index, triangle in enumerate(geometry.triangles)
                )
            else:
                selection_start = selection_cursor
                while (
                    selection_cursor < len(selected_indices)
                    and selected_indices[selection_cursor] < geometry_end
                ):
                    selection_cursor += 1
                triangle_items = (
                    (
                        selected_indices[position],
                        geometry.triangles[
                            selected_indices[position] - geometry_start
                        ],
                    )
                    for position in range(selection_start, selection_cursor)
                )
            for ordinal, triangle in triangle_items:
                raw_0 = geometry.vertices[triangle[0]]
                raw_1 = geometry.vertices[triangle[1]]
                raw_2 = geometry.vertices[triangle[2]]
                transformed_0 = screen(raw_0)
                transformed_1 = screen(raw_1)
                transformed_2 = screen(raw_2)
                ax = raw_1[0] - raw_0[0]
                ay = raw_1[1] - raw_0[1]
                az = raw_1[2] - raw_0[2]
                bx = raw_2[0] - raw_0[0]
                by = raw_2[1] - raw_0[1]
                bz = raw_2[2] - raw_0[2]
                nx, ny, nz = (
                    ay * bz - az * by,
                    az * bx - ax * bz,
                    ax * by - ay * bx,
                )
                if normalized_mode == "materials":
                    # Flat material IDs do not consume the normal-derived value.
                    light = 1.0
                else:
                    magnitude = math.sqrt(nx * nx + ny * ny + nz * nz)
                    diffuse = 0.0 if magnitude <= 1e-12 else abs(
                        (nx / magnitude) * directional[0]
                        + (ny / magnitude) * directional[1]
                        + (nz / magnitude) * directional[2]
                    )
                    # Two-sided Lambert shading avoids turning valid diagnostic
                    # surfaces black when source winding conventions differ.
                    light = min(1.0, 0.36 + (0.64 * diffuse))
                if use_interactive_cache and normalized_mode != "materials":
                    prepared_triangles.append((
                        ordinal, geometry_index, triangle,
                        raw_0, raw_1, raw_2, light,
                    ))
                rendered.append((
                    (transformed_0[2] + transformed_1[2] + transformed_2[2]) / 3.0,
                    ordinal,
                    geometry_index,
                    triangle,
                    (
                        (transformed_0[0], transformed_0[1]),
                        (transformed_1[0], transformed_1[1]),
                        (transformed_2[0], transformed_2[1]),
                    ),
                    light,
                ))
            global_triangle = geometry_end
        if prepared_triangles and interactive_triangle_cache is not None:
            assert interactive_cache_key is not None
            interactive_triangle_cache.pop(interactive_cache_key, None)
            interactive_triangle_cache[interactive_cache_key] = tuple(
                prepared_triangles
            )
            while len(interactive_triangle_cache) > 4:
                interactive_triangle_cache.pop(next(iter(interactive_triangle_cache)))
    rendered.sort(key=lambda item: (item[0], item[1]))

    # Boundary edges are computed only for complete, normally bounded geometry.
    # A sampled mesh has artificial holes, while building a multi-million-entry
    # edge map would undermine the safety of opt-in Full renders near the scene cap.
    boundary_counts: dict[tuple[int, int, int], int] = {}
    trace_boundaries = (
        selected_indices is None
        and total_triangles <= MAX_RENDERED_TRIANGLES
        and normalized_mode != "wireframe"
    )
    if trace_boundaries:
        for _depth, _ordinal, geometry_index, triangle, *_rest in rendered:
            for first, second in (
                (triangle[0], triangle[1]),
                (triangle[1], triangle[2]),
                (triangle[2], triangle[0]),
            ):
                edge = (geometry_index, min(first, second), max(first, second))
                boundary_counts[edge] = boundary_counts.get(edge, 0) + 1

    image = _model_render_background().copy()
    draw = ImageDraw.Draw(image)
    sample_underlay = selected_indices is not None and normalized_mode == "shaded"
    if sample_underlay:
        silhouette_points: list[tuple[float, float]] = []
        weighted_color = [0.0, 0.0, 0.0]
        weighted_triangles = 0
        silhouette_indices = (
            _uniform_sample_indices(vertex_count, INTERACTIVE_SILHOUETTE_POINTS)
            if normalized_quality == "interactive"
            else None
        )
        silhouette_cursor = 0
        global_vertex = 0
        for geometry_index, geometry in enumerate(geometries):
            if not geometry.vertices:
                continue
            geometry_vertex_end = global_vertex + len(geometry.vertices)
            if normalized_quality != "interactive":
                # Preserve the final/full preview's established silhouette.
                stride = max(1, math.ceil(len(geometry.vertices) / 256))
                points = geometry.vertices[::stride]
            elif silhouette_indices is None:
                points = geometry.vertices
            else:
                selection_start = silhouette_cursor
                while (
                    silhouette_cursor < len(silhouette_indices)
                    and silhouette_indices[silhouette_cursor] < geometry_vertex_end
                ):
                    silhouette_cursor += 1
                points = tuple(
                    geometry.vertices[
                        silhouette_indices[position] - global_vertex
                    ]
                    for position in range(selection_start, silhouette_cursor)
                )
            silhouette_points.extend(
                (projected[0], projected[1])
                for point in points
                for projected in (screen(point),)
            )
            color = geometry_render_materials[geometry_index][2]
            weight = max(1, len(geometry.triangles))
            for channel in range(3):
                weighted_color[channel] += color[channel] * weight
            weighted_triangles += weight
            global_vertex = geometry_vertex_end
        hull = _convex_hull(silhouette_points)
        if len(hull) >= 3 and weighted_triangles:
            average = tuple(
                round(value / weighted_triangles) for value in weighted_color
            )
            underlay = _shade_color(average, 0.48)
            draw.polygon(hull, fill=underlay)
            draw.line((*hull, hull[0]), fill=_shade_color(average, 0.62), width=1)
    for (
        _depth, _ordinal, geometry_index, triangle, polygon, light,
    ) in rendered:
        base_color = geometry_render_materials[geometry_index][2]
        if normalized_mode == "wireframe":
            edge_color = _shade_color(base_color, 0.72 + (0.28 * light))
            draw.line((*polygon, polygon[0]), fill=edge_color, width=1)
            continue
        fill = (
            base_color
            if normalized_mode == "materials"
            else _shade_color(base_color, light)
        )
        # Shaded intentionally has no per-triangle outline. Only true open edges
        # and geometry/material boundaries receive a restrained silhouette.
        draw.polygon(polygon, fill=fill)
        if trace_boundaries:
            for first, second, start, end in (
                (triangle[0], triangle[1], polygon[0], polygon[1]),
                (triangle[1], triangle[2], polygon[1], polygon[2]),
                (triangle[2], triangle[0], polygon[2], polygon[0]),
            ):
                edge = (geometry_index, min(first, second), max(first, second))
                if boundary_counts.get(edge) == 1:
                    draw.line(
                        (*start, *end), fill=_shade_color(fill, 0.48), width=1,
                    )
    if not rendered:
        for geometry_index, geometry in enumerate(geometries):
            point_color = geometry_render_materials[geometry_index][2]
            points = [screen(point) for point in geometry.vertices]
            for point in points[::max(1, len(points) // 12_000)]:
                x, y, _depth = point
                draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=point_color)
    draw.text(
        (38, 24), f"{title} · {normalized_mode.upper()}  |  {name[:64]}",
        fill="#E8F2EC",
    )
    draw.text(
        (38, height - 32),
        f"{vertex_count:,} vertices  |  {len(rendered):,}/{total_triangles:,} triangles  |  "
        f"{len(geometries):,} {geometry_label}  |  "
        f"yaw {camera_yaw:.0f}°  pitch {camera_pitch:.0f}°",
        fill="#AFC5B9",
    )
    lods: dict[str, int] = {}
    for geometry in geometries:
        lods[geometry.lod] = lods.get(geometry.lod, 0) + 1
    material_records = {
        (identity, semantic)
        for identity, semantic, _color in geometry_render_materials
    }
    return image, {
        "model_geometry_count": len(geometries),
        "model_vertex_count": vertex_count,
        "model_triangle_count": total_triangles,
        "model_lods": ", ".join(f"{name}: {count}" for name, count in sorted(lods.items())),
        "model_bounds": " x ".join(
            f"{maxima[axis] - minima[axis]:.4g}" for axis in range(3)
        ),
        "model_preview": "isometric geometry diagnostic",
        "model_render_mode": normalized_mode,
        "model_render_quality": normalized_quality,
        "model_render_triangle_budget": configured_budget,
        "model_rendered_triangle_count": len(rendered),
        "model_render_skipped_triangle_count": total_triangles - len(rendered),
        "model_render_sampled": selected_indices is not None,
        "model_render_sample_underlay": sample_underlay,
        "model_render_material_count": len({item[0] for item in material_records}),
        "model_render_semantic_materials": ", ".join(sorted({
            item[1] for item in material_records
        })) or "none",
        "model_render_depth_ordering": "far-to-near triangle painter",
        "model_render_lighting": (
            "two-sided Lambert; ambient 0.36, directional 0.64"
            if normalized_mode == "shaded"
            else "flat material IDs" if normalized_mode == "materials"
            else "edge-only material wireframe"
        ),
        "model_render_output_size": f"{width} x {height}",
        "model_render_view_box": (view_left, view_top, view_right, view_bottom),
        "model_render_bounds_source": (
            "cached selection bounds" if supplied_bounds else "computed geometry bounds"
        ),
        "model_render_fidelity": (
            "decoded geometry and material references only; game shaders, "
            "textures, reflections, and skinning are not reproduced"
        ),
    }


def _encode_model_render(image: Image.Image, quality: str) -> bytes:
    """Encode a model frame for callers that require the stable PNG contract."""
    output = io.BytesIO()
    if quality == "interactive":
        image.save(output, format="PNG", compress_level=1)
    else:
        image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _render_model_wireframe(*args: Any, **kwargs: Any) -> tuple[bytes, dict[str, Any]]:
    """Compatibility wrapper for collision/native preview byte consumers."""
    image, metadata = _render_model_image(*args, **kwargs)
    return _encode_model_render(
        image, str(metadata["model_render_quality"]),
    ), metadata


def _safe_codewalker_xml(xml: Path) -> etree._ElementTree:
    size = xml.stat().st_size
    if not 0 < size <= MAX_MODEL_XML_BYTES:
        raise ValueError("CodeWalker XML exceeds the guarded preview limit")
    with xml.open("rb") as stream:
        prefix = stream.read(65_536).upper()
    if b"<!DOCTYPE" in prefix or b"<!ENTITY" in prefix:
        raise ValueError("CodeWalker XML contains a prohibited DTD or entity declaration")
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False,
        recover=False, huge_tree=True,
    )
    tree = etree.parse(str(xml), parser)
    if tree.docinfo.doctype:
        raise ValueError("CodeWalker XML contains a prohibited document type")
    return tree


def _semantic_xml_sha256(xml: Path) -> str:
    """Hash parsed XML while ignoring serialization-only indentation."""
    tree = _safe_codewalker_xml(xml)
    for node in tree.iter():
        if node.text is not None and not node.text.strip():
            node.text = None
        if node.tail is not None and not node.tail.strip():
            node.tail = None
    canonical = etree.tostring(
        tree, method="c14n", exclusive=False, with_comments=False,
    )
    return hashlib.sha256(canonical).hexdigest()


def _awc_preview_from_xml(
    xml: Path, assets: Path,
) -> tuple[dict[str, Any], str | None]:
    """Summarize streams and exported wave payloads in a bounded AWC XML export."""
    try:
        root = _safe_codewalker_xml(xml).getroot()
        streams = root.xpath(
            "./*[local-name()='Streams']/*[local-name()='Item']"
        )
        if len(streams) > MAX_NATIVE_WORKSPACE_FILES:
            raise ValueError("AWC preview exceeds the guarded stream limit")
        codecs: dict[str, int] = {}
        sample_rates: set[int] = set()
        total_samples = 0
        total_seconds = 0.0
        looped = 0
        peak_streams = 0
        file_names: list[str] = []
        for stream in streams:
            filename = _child_text(stream, "FileName")
            if filename:
                safe_name = Path(filename).name
                if safe_name != filename or safe_name in {".", ".."}:
                    raise ValueError("AWC XML contains an unsafe stream filename")
                file_names.append(safe_name)
            format_chunks = stream.xpath(
                "./*[local-name()='Chunks']/*[local-name()='Item']"
                "[*[local-name()='Type' and normalize-space(text())='format']]"
            )
            stream_samples = 0
            stream_rate = 0
            for chunk in format_chunks:
                codec = _child_text(chunk, "Codec") or "Unknown"
                codecs[codec] = codecs.get(codec, 0) + 1
                samples = int(_numeric_child(
                    chunk, "Samples", context="AWC stream", default=0,
                ))
                rate = int(_numeric_child(
                    chunk, "SampleRate", context="AWC stream", default=0,
                ))
                stream_samples = max(stream_samples, samples)
                stream_rate = max(stream_rate, rate)
                loop_begin = int(_numeric_child(
                    chunk, "LoopBegin", context="AWC stream", default=0,
                ))
                loop_end = int(_numeric_child(
                    chunk, "LoopEnd", context="AWC stream", default=0,
                ))
                if loop_end > loop_begin:
                    looped += 1
            if stream_rate > 0:
                sample_rates.add(stream_rate)
                total_seconds += stream_samples / stream_rate
            total_samples += stream_samples
            if stream.xpath(
                "./*[local-name()='Chunks']/*[local-name()='Item']"
                "[*[local-name()='Type' and normalize-space(text())='peak']]"
            ):
                peak_streams += 1
        asset_files = {
            item.name: item for item in assets.iterdir()
            if item.is_file() and not item.is_symlink()
        } if assets.is_dir() and not assets.is_symlink() else {}
        missing = sorted(set(file_names) - set(asset_files))
        metadata: dict[str, Any] = {
            "audio_stream_count": len(streams),
            "audio_codec_counts": ", ".join(
                f"{name}: {count}" for name, count in sorted(codecs.items())
            ) or "None",
            "audio_sample_rates_hz": ", ".join(
                str(value) for value in sorted(sample_rates)
            ) or "None",
            "audio_total_samples": total_samples,
            "audio_total_duration_seconds": round(total_seconds, 3),
            "audio_looped_streams": looped,
            "audio_peak_streams": peak_streams,
            "audio_wave_files": len(asset_files),
            "audio_wave_bytes": sum(item.stat().st_size for item in asset_files.values()),
            "audio_single_channel_encrypted": _child_value_text(
                root, "SingleChannelEncrypt"
            ).casefold() == "true",
            "audio_multi_channel_encrypted": _child_value_text(
                root, "MultiChannelEncrypt"
            ).casefold() == "true",
        }
        warning = (
            "AWC export did not produce wave payloads for: " + ", ".join(missing)
            if missing else None
        )
        return metadata, warning
    except (
        OSError, ValueError, etree.XMLSyntaxError, OverflowError,
    ) as exc:
        return {}, f"AWC stream preview unavailable: {exc}"


def _model_scene_from_xml(
    xml: Path, name: str,
) -> tuple[NativeModelScene | None, dict[str, Any], str | None]:
    """Decode a bounded reusable model scene from structured native XML."""
    try:
        tree = _safe_codewalker_xml(xml)
        root = tree.getroot()
        materials = _model_materials(root)
        fragment_placements = _fragment_child_geometry_placements(root)
        geometries: list[_ModelGeometry] = []
        total_vertices = 0
        total_triangles = 0
        skipped_layouts = 0
        transformed_fragment_geometries = 0
        mirrored_fragment_geometries = 0
        assembled_fragment_components: set[str] = set()
        for ordinal, vertex_buffer in enumerate(
            root.xpath(".//*[local-name()='VertexBuffer']")
        ):
            geometry = _read_model_geometry(
                vertex_buffer, ordinal=ordinal, materials=materials,
            )
            if geometry is None:
                skipped_layouts += 1
                continue
            expanded = [geometry]
            placement = fragment_placements.get(vertex_buffer)
            if placement is not None:
                expanded = [_transform_model_geometry(
                    geometry, placement.matrix, placement.component,
                )]
                transformed_fragment_geometries += 1
                assembled_fragment_components.add(placement.component)
                if placement.mirrored_matrix is not None:
                    expanded.append(_transform_model_geometry(
                        geometry, placement.mirrored_matrix,
                        placement.mirrored_component,
                    ))
                    mirrored_fragment_geometries += 1
                    assembled_fragment_components.add(placement.mirrored_component)
            for expanded_geometry in expanded:
                total_vertices += len(expanded_geometry.vertices)
                total_triangles += len(expanded_geometry.triangles)
                if total_vertices > MAX_MODEL_VERTICES:
                    raise ValueError("Model preview exceeds the guarded vertex limit")
                if total_triangles > MAX_MODEL_TRIANGLES:
                    raise ValueError("Model preview exceeds the guarded triangle limit")
                geometries.append(expanded_geometry)
        if not geometries:
            return None, {
                "model_drawable_count": _model_drawable_count(root),
                "model_preview": "No supported position buffers were found",
            }, None
        metadata = {}
        metadata["model_drawable_count"] = _model_drawable_count(root)
        metadata.update(_model_resource_metadata(root))
        metadata["model_component_count"] = len({
            (item.component, item.lod) for item in geometries
        })
        metadata["model_material_binding_count"] = sum(
            item.material_index is not None for item in geometries
        )
        if transformed_fragment_geometries:
            metadata["model_fragment_child_transformed_geometry_count"] = (
                transformed_fragment_geometries
            )
            metadata["model_fragment_mirrored_geometry_count"] = (
                mirrored_fragment_geometries
            )
            metadata["model_fragment_assembled_components"] = ", ".join(sorted(
                assembled_fragment_components, key=str.casefold,
            ))
        if skipped_layouts:
            metadata["model_skipped_buffers"] = skipped_layouts
        scene = NativeModelScene(
            name, tuple(geometries), metadata, materials=materials,
        )
        return scene, metadata, None
    except (OSError, ValueError, etree.XMLSyntaxError, OverflowError) as exc:
        return None, {}, f"Model scene unavailable: {exc}"


def load_native_model_scene(
    xml: str | Path, *, name: str | None = None,
) -> tuple[NativeModelScene | None, dict[str, Any], str | None]:
    """Load a guarded CodeWalker model XML document for SDK workbenches.

    The native asset inspector historically kept this decoder private because
    it was used only to produce preview images.  Model/material authoring also
    needs the exact same bounded parser and scene semantics, so expose one
    narrow read-only entry point rather than letting each workbench grow a
    subtly different XML decoder.
    """
    source = Path(xml).expanduser().resolve()
    return _model_scene_from_xml(source, name or source.name)


def _model_preview_from_xml(
    xml: Path, name: str,
) -> tuple[bytes | None, dict[str, Any], str | None]:
    """Build the default diagnostic preview from a reusable model scene."""
    scene, metadata, warning = _model_scene_from_xml(xml, name)
    if scene is None:
        return None, metadata, warning
    try:
        image, rendered = scene.render()
        return image, rendered, warning
    except ValueError as exc:
        return None, metadata, f"Model preview unavailable: {exc}"


def _vector_attributes(
    element: etree._Element | None,
) -> tuple[float, float, float]:
    if element is None:
        return 0.0, 0.0, 0.0
    try:
        point = tuple(float(element.get(axis, "0")) for axis in ("x", "y", "z"))
    except ValueError as exc:
        raise ValueError("Collision geometry center is non-numeric") from exc
    if not all(math.isfinite(value) for value in point):
        raise ValueError("Collision geometry center is non-finite")
    return point[0], point[1], point[2]


def _collision_vertices(
    element: etree._Element, center: tuple[float, float, float],
) -> tuple[tuple[float, float, float], ...]:
    vertices: list[tuple[float, float, float]] = []
    for line in (element.text or "").splitlines():
        fields = [part.strip() for part in line.split(",")]
        if not fields or all(not part for part in fields):
            continue
        if len(fields) != 3:
            raise ValueError("Collision vertex row does not contain three coordinates")
        try:
            local = tuple(float(value) for value in fields)
        except ValueError as exc:
            raise ValueError("Collision vertex contains a non-numeric coordinate") from exc
        if not all(math.isfinite(value) for value in local):
            raise ValueError("Collision vertex contains a non-finite coordinate")
        vertices.append(tuple(local[axis] + center[axis] for axis in range(3)))
        if len(vertices) > MAX_MODEL_VERTICES:
            raise ValueError("Collision preview exceeds the guarded vertex limit")
    return tuple(vertices)


def _collision_index(
    polygon: etree._Element, attribute: str, vertex_count: int,
) -> int:
    value = polygon.get(attribute)
    if value is None:
        raise ValueError(f"Collision {polygon.tag} is missing {attribute}")
    try:
        index = int(value, 10)
    except ValueError as exc:
        raise ValueError(f"Collision {polygon.tag} has a non-integer index") from exc
    if index < 0 or index >= vertex_count:
        raise ValueError(f"Collision {polygon.tag} references a missing vertex")
    return index


def _collision_preview_from_xml(
    xml: Path, name: str,
) -> tuple[bytes | None, dict[str, Any], str | None]:
    """Render bounded YBN triangle and primitive diagnostics."""
    try:
        root = _safe_codewalker_xml(xml).getroot()
        geometries: list[_ModelGeometry] = []
        polygon_counts: dict[str, int] = {}
        skipped_polygons = 0
        total_vertices = 0
        total_render_triangles = 0
        for vertices_element in root.xpath(".//*[local-name()='Vertices']"):
            owner = vertices_element.getparent()
            if owner is None:
                continue
            polygons = owner.find("./Polygons")
            if polygons is None:
                continue
            center = _vector_attributes(owner.find("./GeometryCenter"))
            vertices = _collision_vertices(vertices_element, center)
            if not vertices:
                continue
            triangles: list[tuple[int, int, int]] = []
            for polygon in polygons:
                if not isinstance(polygon.tag, str):
                    continue
                kind = _local_name(polygon)
                polygon_counts[kind] = polygon_counts.get(kind, 0) + 1
                if kind == "Triangle":
                    triangles.append(tuple(
                        _collision_index(polygon, attribute, len(vertices))
                        for attribute in ("v1", "v2", "v3")
                    ))
                elif kind == "Box":
                    box = tuple(
                        _collision_index(polygon, attribute, len(vertices))
                        for attribute in ("v1", "v2", "v3", "v4")
                    )
                    # Four YBN box control vertices describe one oriented primitive.
                    # A tetrahedral diagnostic hull exposes its placement without
                    # claiming to be the exact physics-engine surface tessellation.
                    triangles.extend((
                        (box[0], box[1], box[2]), (box[0], box[1], box[3]),
                        (box[0], box[2], box[3]), (box[1], box[2], box[3]),
                    ))
                elif kind not in {"Sphere", "Capsule", "Cylinder"}:
                    skipped_polygons += 1
            total_vertices += len(vertices)
            total_render_triangles += len(triangles)
            if total_vertices > MAX_MODEL_VERTICES:
                raise ValueError("Collision preview exceeds the guarded vertex limit")
            if total_render_triangles > MAX_MODEL_TRIANGLES:
                raise ValueError("Collision preview exceeds the guarded triangle limit")
            geometries.append(_ModelGeometry(vertices, tuple(triangles), _local_name(owner)))
        material_count = len(root.xpath(
            ".//*[local-name()='Materials']/*[local-name()='Item']"
        ))
        metadata: dict[str, Any] = {
            "collision_geometry_count": len(geometries),
            "collision_vertex_count": total_vertices,
            "collision_polygon_count": sum(polygon_counts.values()),
            "collision_material_count": material_count,
            "collision_primitives": ", ".join(
                f"{kind}: {count}" for kind, count in sorted(polygon_counts.items())
            ) or "none",
        }
        if skipped_polygons:
            metadata["collision_skipped_polygons"] = skipped_polygons
        if not geometries:
            metadata["collision_preview"] = "No supported collision geometry was found"
            return None, metadata, None
        image, rendered = _render_model_wireframe(
            geometries, name, title="COLLISION PREVIEW", geometry_label="collision groups",
        )
        metadata.update({
            "collision_bounds": rendered["model_bounds"],
            "collision_render_triangles": rendered["model_triangle_count"],
            "collision_preview": "isometric geometry diagnostic",
        })
        return image, metadata, None
    except (OSError, ValueError, etree.XMLSyntaxError, OverflowError) as exc:
        return None, {}, f"Collision preview unavailable: {exc}"


def _direct_child(
    parent: etree._Element, name: str,
) -> etree._Element | None:
    for child in parent:
        if isinstance(child.tag, str) and _local_name(child) == name:
            return child
    return None


def _child_value(parent: etree._Element, name: str, default: float) -> float:
    child = _direct_child(parent, name)
    if child is None:
        return default
    try:
        value = float(child.get("value", str(default)))
    except ValueError as exc:
        raise ValueError(f"YMAP {name} value is non-numeric") from exc
    if not math.isfinite(value):
        raise ValueError(f"YMAP {name} value is non-finite")
    return value


def _map_entities(root: etree._Element) -> tuple[list[_MapEntity], int]:
    container = _direct_child(root, "entities")
    if container is None:
        return [], 0
    entities: list[_MapEntity] = []
    skipped = 0
    for item in container:
        if not isinstance(item.tag, str) or _local_name(item) != "Item":
            continue
        position_element = _direct_child(item, "position")
        if position_element is None:
            skipped += 1
            continue
        position = _vector_attributes(position_element)
        archetype_element = _direct_child(item, "archetypeName")
        archetype = (
            (archetype_element.text or "").strip()
            if archetype_element is not None else ""
        ) or "(unnamed archetype)"
        parent_element = _direct_child(item, "parentIndex")
        try:
            parent = int(parent_element.get("value", "-1")) if parent_element is not None else -1
        except ValueError as exc:
            raise ValueError("YMAP parentIndex is non-integer") from exc
        rotation = _direct_child(item, "rotation")
        if rotation is None:
            yaw = 0.0
        else:
            try:
                qx, qy, qz, qw = (
                    float(rotation.get(axis, default))
                    for axis, default in (("x", "0"), ("y", "0"), ("z", "0"), ("w", "1"))
                )
            except ValueError as exc:
                raise ValueError("YMAP entity rotation is non-numeric") from exc
            if not all(math.isfinite(value) for value in (qx, qy, qz, qw)):
                raise ValueError("YMAP entity rotation is non-finite")
            yaw = math.atan2(
                2.0 * ((qw * qz) + (qx * qy)),
                1.0 - (2.0 * ((qy * qy) + (qz * qz))),
            )
        scale = max(0.01, min(1000.0, _child_value(item, "scaleXY", 1.0)))
        entities.append(_MapEntity(archetype, position, parent, yaw, scale))
        if len(entities) > MAX_MAP_ENTITIES:
            raise ValueError("YMAP preview exceeds the guarded entity limit")
    return entities, skipped


def _map_item_count(root: etree._Element, name: str) -> int:
    container = _direct_child(root, name)
    if container is None:
        return 0
    return sum(
        1 for item in container
        if isinstance(item.tag, str) and _local_name(item) == "Item"
    )


def _map_colour(archetype: str) -> tuple[int, int, int]:
    digest = hashlib.sha256(archetype.encode("utf-8", errors="replace")).digest()
    return 70 + (digest[0] % 140), 75 + (digest[1] % 130), 85 + (digest[2] % 125)


def _render_map_entities(
    entities: list[_MapEntity], name: str,
) -> tuple[bytes, dict[str, Any]]:
    minima = tuple(min(entity.position[axis] for entity in entities) for axis in range(3))
    maxima = tuple(max(entity.position[axis] for entity in entities) for axis in range(3))
    width, height = 960, 680
    left, top, right, bottom = 48, 78, 748, 616
    span_x = max(maxima[0] - minima[0], 1.0)
    span_y = max(maxima[1] - minima[1], 1.0)
    padding_x = max(span_x * 0.04, 0.5)
    padding_y = max(span_y * 0.04, 0.5)
    world_left, world_right = minima[0] - padding_x, maxima[0] + padding_x
    world_bottom, world_top = minima[1] - padding_y, maxima[1] + padding_y

    def screen(position: tuple[float, float, float]) -> tuple[float, float]:
        x = left + ((position[0] - world_left) / (world_right - world_left)) * (right - left)
        y = bottom - ((position[1] - world_bottom) / (world_top - world_bottom)) * (bottom - top)
        return x, y

    image = Image.new("RGB", (width, height), "#101714")
    draw = ImageDraw.Draw(image)
    draw.rectangle((left, top, right, bottom), fill="#121c18", outline="#31453a")
    for division in range(1, 5):
        x = left + ((right - left) * division / 5)
        y = top + ((bottom - top) * division / 5)
        draw.line((x, top, x, bottom), fill="#1e2d26")
        draw.line((left, y, right, y), fill="#1e2d26")
    draw.text((48, 24), f"YMAP PLACEMENT  |  {name[:68]}", fill="#E8F2EC")
    draw.text((left, bottom + 10), f"X {minima[0]:.2f} .. {maxima[0]:.2f}", fill="#91AA9D")
    draw.text((right - 178, bottom + 10), f"Y {minima[1]:.2f} .. {maxima[1]:.2f}", fill="#91AA9D")

    stride = max(1, math.ceil(len(entities) / MAX_RENDERED_MAP_ENTITIES))
    sampled_indexes = range(0, len(entities), stride)
    for index in sampled_indexes:
        entity = entities[index]
        if 0 <= entity.parent < len(entities):
            draw.line(
                (*screen(entity.position), *screen(entities[entity.parent].position)),
                fill="#455b50", width=1,
            )
    for index in sampled_indexes:
        entity = entities[index]
        x, y = screen(entity.position)
        colour = _map_colour(entity.archetype)
        radius = max(2.0, min(7.0, 2.5 + math.log2(max(1.0, entity.scale))))
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=colour)
        direction = 5.0 + radius
        draw.line((
            x, y,
            x + (math.sin(entity.yaw) * direction),
            y - (math.cos(entity.yaw) * direction),
        ), fill="#E8F2EC", width=1)
        if entity.parent < 0:
            draw.ellipse(
                (x - radius - 2, y - radius - 2, x + radius + 2, y + radius + 2),
                outline="#91DDB4",
            )

    counts: dict[str, int] = {}
    for entity in entities:
        counts[entity.archetype] = counts.get(entity.archetype, 0) + 1
    draw.text((776, 78), "TOP ARCHETYPES", fill="#E8F2EC")
    for row, (archetype, count) in enumerate(sorted(
        counts.items(), key=lambda item: (-item[1], item[0].casefold()),
    )[:14]):
        y = 106 + (row * 29)
        colour = _map_colour(archetype)
        draw.rectangle((776, y + 2, 786, y + 12), fill=colour)
        draw.text((794, y), archetype[:22], fill="#C6D8CE")
        draw.text((910, y), str(count), fill="#91AA9D")
    roots = sum(1 for entity in entities if entity.parent < 0)
    draw.text(
        (48, height - 25),
        f"{len(entities):,} entities  |  {len(counts):,} archetypes  |  "
        f"{roots:,} roots  |  top-down diagnostic view",
        fill="#AFC5B9",
    )
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    valid_links = sum(
        1 for entity in entities if 0 <= entity.parent < len(entities)
    )
    invalid_links = sum(
        1 for entity in entities if entity.parent >= len(entities)
    )
    return output.getvalue(), {
        "map_entity_count": len(entities),
        "map_archetype_count": len(counts),
        "map_root_entities": roots,
        "map_parent_links": valid_links,
        "map_invalid_parent_links": invalid_links,
        "map_bounds": " x ".join(
            f"{maxima[axis] - minima[axis]:.4g}" for axis in range(3)
        ),
        "map_center": ", ".join(
            f"{(minima[axis] + maxima[axis]) / 2.0:.4f}" for axis in range(3)
        ),
        "map_preview": "top-down entity placement diagnostic",
    }


def _map_preview_from_xml(
    xml: Path, name: str,
) -> tuple[bytes | None, dict[str, Any], str | None]:
    """Render a bounded YMAP entity placement overview."""
    try:
        root = _safe_codewalker_xml(xml).getroot()
        entities, skipped = _map_entities(root)
        name_element = _direct_child(root, "name")
        metadata: dict[str, Any] = {
            "map_name": ((name_element.text or "").strip() if name_element is not None else ""),
            "map_car_generators": _map_item_count(root, "carGenerators"),
            "map_box_occluders": _map_item_count(root, "boxOccluders"),
            "map_occlude_models": _map_item_count(root, "occludeModels"),
            "map_timecycle_modifiers": _map_item_count(root, "timeCycleModifiers"),
        }
        if skipped:
            metadata["map_skipped_entities"] = skipped
        if not entities:
            metadata.update({
                "map_entity_count": 0,
                "map_archetype_count": 0,
                "map_preview": "No positioned entities were found",
            })
            return None, metadata, None
        image, rendered = _render_map_entities(entities, name)
        metadata.update(rendered)
        return image, metadata, None
    except (OSError, ValueError, etree.XMLSyntaxError, OverflowError) as exc:
        return None, {}, f"Map preview unavailable: {exc}"


def _numeric_child(
    parent: etree._Element, name: str, *, context: str,
    integer: bool = False, default: int | float | None = None,
) -> int | float:
    child = _direct_child(parent, name)
    if child is None:
        if default is not None:
            return default
        raise ValueError(f"{context} is missing {name}")
    raw = child.get("value")
    if raw is None:
        raw = (child.text or "").strip()
    try:
        value = int(raw, 10) if integer else float(raw)
    except ValueError as exc:
        kind = "integer" if integer else "numeric"
        raise ValueError(f"{context} {name} is non-{kind}") from exc
    if not integer and not math.isfinite(value):
        raise ValueError(f"{context} {name} is non-finite")
    return value


def _position_attributes(
    element: etree._Element | None, *, context: str, dimensions: int = 3,
) -> tuple[float, float, float]:
    if element is None:
        raise ValueError(f"{context} position is missing")
    axes = ("x", "y", "z")[:dimensions]
    try:
        values = [float(element.get(axis, "0")) for axis in axes]
    except ValueError as exc:
        raise ValueError(f"{context} position is non-numeric") from exc
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"{context} position is non-finite")
    while len(values) < 3:
        values.append(0.0)
    return values[0], values[1], values[2]


def _raw_vector_rows(
    element: etree._Element | None, *, context: str,
) -> tuple[tuple[float, float, float], ...]:
    if element is None:
        return ()
    points: list[tuple[float, float, float]] = []
    for line in (element.text or "").splitlines():
        fields = [part.strip() for part in line.split(",")]
        if not fields or all(not field for field in fields):
            continue
        if len(fields) != 3:
            raise ValueError(f"{context} vertex row does not contain three coordinates")
        try:
            point = tuple(float(field) for field in fields)
        except ValueError as exc:
            raise ValueError(f"{context} vertex is non-numeric") from exc
        if not all(math.isfinite(value) for value in point):
            raise ValueError(f"{context} vertex is non-finite")
        points.append((point[0], point[1], point[2]))
    return tuple(points)


def _raw_integer_values(
    element: etree._Element | None, *, context: str,
) -> tuple[int, ...]:
    if element is None:
        return ()
    values: list[int] = []
    for token in (element.text or "").replace(",", " ").split():
        try:
            values.append(int(token, 10))
        except ValueError as exc:
            raise ValueError(f"{context} contains a non-integer value") from exc
    return tuple(values)


def _item_children(parent: etree._Element, name: str) -> list[etree._Element]:
    container = _direct_child(parent, name)
    if container is None:
        return []
    return [
        item for item in container
        if isinstance(item.tag, str) and _local_name(item) == "Item"
    ]


def _navmesh_records(
    root: etree._Element,
) -> tuple[list[_NavPolygon], list[_NavPortal], list[_NavPoint], int, int]:
    polygons: list[_NavPolygon] = []
    portals: list[_NavPortal] = []
    points: list[_NavPoint] = []
    total_vertices = 0
    edge_references = 0
    skipped_polygons = 0
    for item in _item_children(root, "Polygons"):
        vertices = _raw_vector_rows(
            _direct_child(item, "Vertices"), context="YNV polygon",
        )
        if not vertices:
            skipped_polygons += 1
            continue
        flags = _raw_integer_values(_direct_child(item, "Flags"), context="YNV flags")
        polygons.append(_NavPolygon(vertices, flags))
        total_vertices += len(vertices)
        edges = _direct_child(item, "Edges")
        edge_references += sum(
            1 for line in (edges.text or "").splitlines() if line.strip()
        ) if edges is not None else 0
        if len(polygons) > MAX_NAV_POLYGONS:
            raise ValueError("YNV preview exceeds the guarded polygon limit")
        if total_vertices > MAX_NAV_VERTICES:
            raise ValueError("YNV preview exceeds the guarded vertex limit")
    for item in _item_children(root, "Portals"):
        portals.append(_NavPortal(
            _position_attributes(
                _direct_child(item, "PositionFrom"), context="YNV portal from",
            ),
            _position_attributes(
                _direct_child(item, "PositionTo"), context="YNV portal to",
            ),
            int(_numeric_child(
                item, "Type", context="YNV portal", integer=True, default=0,
            )),
        ))
        if len(portals) > MAX_NAV_PORTALS:
            raise ValueError("YNV preview exceeds the guarded portal limit")
    for item in _item_children(root, "Points"):
        points.append(_NavPoint(
            _position_attributes(
                _direct_child(item, "Position"), context="YNV point",
            ),
            float(_numeric_child(
                item, "Angle", context="YNV point", default=0.0,
            )),
            int(_numeric_child(
                item, "Type", context="YNV point", integer=True, default=0,
            )),
        ))
        if len(points) > MAX_NAV_POINTS:
            raise ValueError("YNV preview exceeds the guarded point limit")
    return polygons, portals, points, edge_references, skipped_polygons


def _nav_colour(flags: tuple[int, ...]) -> tuple[int, int, int]:
    signature = ",".join(str(flag) for flag in flags).encode("ascii", errors="replace")
    digest = hashlib.sha256(signature).digest()
    return 31 + (digest[0] % 38), 72 + (digest[1] % 70), 63 + (digest[2] % 55)


def _render_navmesh(
    polygons: list[_NavPolygon], portals: list[_NavPortal], points: list[_NavPoint],
    name: str,
) -> tuple[bytes, dict[str, Any]]:
    all_points = [point for polygon in polygons for point in polygon.vertices]
    all_points.extend(portal.position_from for portal in portals)
    all_points.extend(portal.position_to for portal in portals)
    all_points.extend(point.position for point in points)
    minima = tuple(min(point[axis] for point in all_points) for axis in range(3))
    maxima = tuple(max(point[axis] for point in all_points) for axis in range(3))
    width, height = 960, 680
    left, top, right, bottom = 48, 78, 748, 616
    span_x = max(maxima[0] - minima[0], 1.0)
    span_y = max(maxima[1] - minima[1], 1.0)
    pad_x, pad_y = max(span_x * 0.03, 0.5), max(span_y * 0.03, 0.5)

    def screen(position: tuple[float, float, float]) -> tuple[float, float]:
        x = left + (
            (position[0] - minima[0] + pad_x) / (span_x + (2 * pad_x))
        ) * (right - left)
        y = bottom - (
            (position[1] - minima[1] + pad_y) / (span_y + (2 * pad_y))
        ) * (bottom - top)
        return x, y

    image = Image.new("RGB", (width, height), "#101714")
    draw = ImageDraw.Draw(image)
    draw.rectangle((left, top, right, bottom), fill="#121c18", outline="#31453a")
    for division in range(1, 5):
        x = left + ((right - left) * division / 5)
        y = top + ((bottom - top) * division / 5)
        draw.line((x, top, x, bottom), fill="#1e2d26")
        draw.line((left, y, right, y), fill="#1e2d26")
    draw.text((48, 24), f"YNV NAVMESH  |  {name[:70]}", fill="#E8F2EC")
    polygon_stride = max(1, math.ceil(len(polygons) / MAX_RENDERED_NAV_POLYGONS))
    for polygon in polygons[::polygon_stride]:
        if len(polygon.vertices) < 3:
            continue
        colour = _nav_colour(polygon.flags)
        draw.polygon(
            [screen(point) for point in polygon.vertices],
            fill=colour, outline=(min(255, colour[0] + 38),
                                  min(255, colour[1] + 62),
                                  min(255, colour[2] + 48)),
        )
    portal_stride = max(1, math.ceil(len(portals) / 30_000))
    for portal in portals[::portal_stride]:
        draw.line(
            (*screen(portal.position_from), *screen(portal.position_to)),
            fill="#F09972", width=2,
        )
    point_stride = max(1, math.ceil(len(points) / 45_000))
    for point in points[::point_stride]:
        x, y = screen(point.position)
        draw.ellipse((x - 2, y - 2, x + 2, y + 2), fill="#E7D875")
        draw.line((
            x, y, x + (math.cos(point.angle) * 7),
            y - (math.sin(point.angle) * 7),
        ), fill="#FFF4A9")
    draw.text((776, 78), "NAVIGATION LAYERS", fill="#E8F2EC")
    legend = (
        ("Polygon surfaces", "#4D9279"),
        ("Portal spans", "#F09972"),
        ("Point nodes", "#E7D875"),
    )
    for row, (label, colour) in enumerate(legend):
        y = 108 + (row * 32)
        draw.rectangle((776, y + 2, 788, y + 14), fill=colour)
        draw.text((798, y), label, fill="#C6D8CE")
    draw.text((776, 220), "BOUNDS", fill="#E8F2EC")
    for row, axis in enumerate("XYZ"):
        draw.text(
            (776, 248 + (row * 26)),
            f"{axis} {minima[row]:.2f} .. {maxima[row]:.2f}", fill="#91AA9D",
        )
    draw.text(
        (48, height - 25),
        f"{len(polygons):,} polygons  |  "
        f"{sum(len(polygon.vertices) for polygon in polygons):,} vertices  |  "
        f"{len(portals):,} portals  |  {len(points):,} points  |  diagnostic view",
        fill="#AFC5B9",
    )
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue(), {
        "navmesh_polygon_count": len(polygons),
        "navmesh_vertex_count": sum(len(polygon.vertices) for polygon in polygons),
        "navmesh_portal_count": len(portals),
        "navmesh_point_count": len(points),
        "navmesh_bounds": " x ".join(
            f"{maxima[axis] - minima[axis]:.4g}" for axis in range(3)
        ),
        "navmesh_preview": "top-down polygon and portal diagnostic",
    }


def _navmesh_preview_from_xml(
    xml: Path, name: str,
) -> tuple[bytes | None, dict[str, Any], str | None]:
    """Render a bounded CodeWalker YNV polygon, portal, and point overview."""
    try:
        root = _safe_codewalker_xml(xml).getroot()
        polygons, portals, points, edge_references, skipped = _navmesh_records(root)
        content = _direct_child(root, "ContentFlags")
        metadata: dict[str, Any] = {
            "navmesh_area_id": int(_numeric_child(
                root, "AreaID", context="YNV", integer=True, default=0,
            )),
            "navmesh_content_flags": (
                (content.text or "").strip() if content is not None else ""
            ),
            "navmesh_edge_references": edge_references,
        }
        if skipped:
            metadata["navmesh_skipped_polygons"] = skipped
        if not polygons and not portals and not points:
            metadata.update({
                "navmesh_polygon_count": 0,
                "navmesh_portal_count": 0,
                "navmesh_point_count": 0,
                "navmesh_preview": "No navigation geometry was found",
            })
            return None, metadata, None
        image, rendered = _render_navmesh(polygons, portals, points, name)
        metadata.update(rendered)
        return image, metadata, None
    except (OSError, ValueError, etree.XMLSyntaxError, OverflowError) as exc:
        return None, {}, f"Navigation mesh preview unavailable: {exc}"


def _path_records(
    root: etree._Element,
) -> tuple[list[_PathNode], list[_PathJunction], int, int, int]:
    declared_vehicle = int(_numeric_child(
        root, "VehicleNodeCount", context="YND", integer=True, default=0,
    ))
    declared_ped = int(_numeric_child(
        root, "PedNodeCount", context="YND", integer=True, default=0,
    ))
    nodes: list[_PathNode] = []
    total_links = 0
    for index, item in enumerate(_item_children(root, "Nodes")):
        links: list[_PathLink] = []
        for link in _item_children(item, "Links"):
            links.append(_PathLink(
                int(_numeric_child(
                    link, "ToAreaID", context="YND link", integer=True,
                )),
                int(_numeric_child(
                    link, "ToNodeID", context="YND link", integer=True,
                )),
                tuple(int(_numeric_child(
                    link, f"Flags{flag}", context="YND link", integer=True,
                    default=0,
                )) for flag in range(3)),
                int(_numeric_child(
                    link, "LinkLength", context="YND link", integer=True,
                    default=0,
                )),
            ))
            total_links += 1
            if total_links > MAX_PATH_LINKS:
                raise ValueError("YND preview exceeds the guarded link limit")
        street = _direct_child(item, "StreetName")
        nodes.append(_PathNode(
            int(_numeric_child(item, "AreaID", context="YND node", integer=True)),
            int(_numeric_child(item, "NodeID", context="YND node", integer=True)),
            ((street.text or "").strip() if street is not None else "") or "(unnamed)",
            _position_attributes(
                _direct_child(item, "Position"), context="YND node",
            ),
            tuple(int(_numeric_child(
                item, f"Flags{flag}", context="YND node", integer=True, default=0,
            )) for flag in range(6)),
            tuple(links),
            index < declared_vehicle,
        ))
        if len(nodes) > MAX_PATH_NODES:
            raise ValueError("YND preview exceeds the guarded node limit")
    junctions: list[_PathJunction] = []
    for item in _item_children(root, "Junctions"):
        position = _position_attributes(
            _direct_child(item, "Position"), context="YND junction", dimensions=2,
        )
        min_z = float(_numeric_child(
            item, "MinZ", context="YND junction", default=0.0,
        ))
        junctions.append(_PathJunction(
            (position[0], position[1], min_z),
            float(_numeric_child(
                item, "MaxZ", context="YND junction", default=min_z,
            )),
            int(_numeric_child(
                item, "SizeX", context="YND junction", integer=True, default=0,
            )),
            int(_numeric_child(
                item, "SizeY", context="YND junction", integer=True, default=0,
            )),
        ))
        if len(junctions) > MAX_PATH_JUNCTIONS:
            raise ValueError("YND preview exceeds the guarded junction limit")
    junction_refs = len(_item_children(root, "JunctionRefs"))
    return nodes, junctions, declared_vehicle, declared_ped, junction_refs


def _render_path_network(
    nodes: list[_PathNode], junctions: list[_PathJunction], name: str,
) -> tuple[bytes, dict[str, Any]]:
    positioned = [node.position for node in nodes]
    positioned.extend(junction.position for junction in junctions)
    minima = tuple(min(point[axis] for point in positioned) for axis in range(3))
    maxima = tuple(max(point[axis] for point in positioned) for axis in range(3))
    width, height = 960, 680
    left, top, right, bottom = 48, 78, 748, 616
    span_x = max(maxima[0] - minima[0], 1.0)
    span_y = max(maxima[1] - minima[1], 1.0)
    pad_x, pad_y = max(span_x * 0.03, 0.5), max(span_y * 0.03, 0.5)

    def screen(position: tuple[float, float, float]) -> tuple[float, float]:
        x = left + (
            (position[0] - minima[0] + pad_x) / (span_x + (2 * pad_x))
        ) * (right - left)
        y = bottom - (
            (position[1] - minima[1] + pad_y) / (span_y + (2 * pad_y))
        ) * (bottom - top)
        return x, y

    lookup: dict[tuple[int, int], _PathNode] = {}
    duplicate_ids = 0
    for node in nodes:
        key = (node.area_id, node.node_id)
        if key in lookup:
            duplicate_ids += 1
        else:
            lookup[key] = node
    internal_links: list[tuple[_PathNode, _PathNode]] = []
    external_links = 0
    for node in nodes:
        for link in node.links:
            target = lookup.get((link.to_area, link.to_node))
            if target is None:
                external_links += 1
            else:
                internal_links.append((node, target))

    image = Image.new("RGB", (width, height), "#101714")
    draw = ImageDraw.Draw(image)
    draw.rectangle((left, top, right, bottom), fill="#121c18", outline="#31453a")
    for division in range(1, 5):
        x = left + ((right - left) * division / 5)
        y = top + ((bottom - top) * division / 5)
        draw.line((x, top, x, bottom), fill="#1e2d26")
        draw.line((left, y, right, y), fill="#1e2d26")
    draw.text((48, 24), f"YND PATH NETWORK  |  {name[:65]}", fill="#E8F2EC")
    link_stride = max(1, math.ceil(len(internal_links) / MAX_RENDERED_PATH_LINKS))
    for source, target in internal_links[::link_stride]:
        draw.line(
            (*screen(source.position), *screen(target.position)),
            fill="#355246", width=1,
        )
    junction_stride = max(1, math.ceil(len(junctions) / 30_000))
    for junction in junctions[::junction_stride]:
        x, y = screen(junction.position)
        radius = max(2, min(7, 2 + max(junction.size_x, junction.size_y) // 4))
        draw.rectangle((x - radius, y - radius, x + radius, y + radius),
                       outline="#F0A36F", fill="#774D35")
    node_stride = max(1, math.ceil(len(nodes) / MAX_RENDERED_PATH_NODES))
    for node in nodes[::node_stride]:
        x, y = screen(node.position)
        colour = "#72D39D" if node.vehicle else "#75A9E7"
        draw.ellipse((x - 1.7, y - 1.7, x + 1.7, y + 1.7), fill=colour)
    draw.text((776, 78), "PATH LAYERS", fill="#E8F2EC")
    legend = (
        ("Vehicle nodes", "#72D39D"),
        ("Ped nodes", "#75A9E7"),
        ("Junctions", "#F0A36F"),
        ("Internal links", "#557564"),
    )
    for row, (label, colour) in enumerate(legend):
        y = 108 + (row * 30)
        draw.rectangle((776, y + 2, 788, y + 14), fill=colour)
        draw.text((798, y), label, fill="#C6D8CE")
    streets: dict[str, int] = {}
    for node in nodes:
        streets[node.street] = streets.get(node.street, 0) + 1
    draw.text((776, 246), "TOP STREET LABELS", fill="#E8F2EC")
    for row, (street, count) in enumerate(sorted(
        streets.items(), key=lambda item: (-item[1], item[0].casefold()),
    )[:10]):
        y = 274 + (row * 27)
        draw.text((776, y), street[:19], fill="#C6D8CE")
        draw.text((914, y), str(count), fill="#91AA9D")
    draw.text(
        (48, height - 25),
        f"{len(nodes):,} nodes  |  {len(internal_links):,} internal links  |  "
        f"{external_links:,} external links  |  {len(junctions):,} junctions  |  diagnostic view",
        fill="#AFC5B9",
    )
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue(), {
        "path_node_count": len(nodes),
        "path_vehicle_nodes": sum(1 for node in nodes if node.vehicle),
        "path_ped_nodes": sum(1 for node in nodes if not node.vehicle),
        "path_link_count": sum(len(node.links) for node in nodes),
        "path_internal_links": len(internal_links),
        "path_external_links": external_links,
        "path_junction_count": len(junctions),
        "path_street_count": len(streets),
        "path_duplicate_node_ids": duplicate_ids,
        "path_bounds": " x ".join(
            f"{maxima[axis] - minima[axis]:.4g}" for axis in range(3)
        ),
        "path_preview": "top-down node, link, and junction diagnostic",
    }


def _path_preview_from_xml(
    xml: Path, name: str,
) -> tuple[bytes | None, dict[str, Any], str | None]:
    """Render a bounded CodeWalker YND node and link overview."""
    try:
        root = _safe_codewalker_xml(xml).getroot()
        nodes, junctions, vehicle_count, ped_count, junction_refs = _path_records(root)
        metadata: dict[str, Any] = {
            "path_declared_vehicle_nodes": vehicle_count,
            "path_declared_ped_nodes": ped_count,
            "path_junction_references": junction_refs,
        }
        if not nodes and not junctions:
            metadata.update({
                "path_node_count": 0,
                "path_link_count": 0,
                "path_junction_count": 0,
                "path_preview": "No path nodes or junctions were found",
            })
            return None, metadata, None
        image, rendered = _render_path_network(nodes, junctions, name)
        metadata.update(rendered)
        metadata["path_declared_count_mismatch"] = (
            (vehicle_count + ped_count) != len(nodes)
        )
        return image, metadata, None
    except (OSError, ValueError, etree.XMLSyntaxError, OverflowError) as exc:
        return None, {}, f"Path network preview unavailable: {exc}"


def _child_text(parent: etree._Element, name: str) -> str:
    child = _direct_child(parent, name)
    return ((child.text or "").strip() if child is not None else "")


def _child_value_text(parent: etree._Element, name: str) -> str:
    child = _direct_child(parent, name)
    if child is None:
        return ""
    return (child.get("value") or child.text or "").strip()


def _archetype_records(root: etree._Element) -> list[_ArchetypeRecord]:
    records: list[_ArchetypeRecord] = []
    for item in _item_children(root, "archetypes"):
        extensions = _direct_child(item, "extensions")
        extension_count = 0 if extensions is None else sum(
            1 for child in extensions
            if isinstance(child.tag, str) and _local_name(child) == "Item"
        )
        records.append(_ArchetypeRecord(
            _child_text(item, "name") or "(unnamed archetype)",
            item.get("type", "CBaseArchetypeDef"),
            _child_text(item, "assetType") or "(unspecified asset type)",
            _child_text(item, "assetName") or "(unspecified asset)",
            _child_text(item, "textureDictionary"),
            _child_text(item, "drawableDictionary"),
            _child_text(item, "physicsDictionary"),
            _child_text(item, "clipDictionary"),
            float(_numeric_child(
                item, "lodDist", context="YTYP archetype", default=0.0,
            )),
            extension_count,
        ))
        if len(records) > MAX_ARCHETYPES:
            raise ValueError("YTYP preview exceeds the guarded archetype limit")
    return records


def _archetype_dependencies(
    record: _ArchetypeRecord,
) -> tuple[tuple[str, str], ...]:
    values = (
        ("Texture dictionary", record.texture_dictionary),
        ("Drawable dictionary", record.drawable_dictionary),
        ("Physics dictionary", record.physics_dictionary),
        ("Clip dictionary", record.clip_dictionary),
    )
    return tuple((kind, value) for kind, value in values if value)


def _archetype_samples(records: list[_ArchetypeRecord]) -> list[_ArchetypeRecord]:
    if len(records) <= MAX_RENDERED_ARCHETYPES:
        return records
    last = len(records) - 1
    indexes = {
        round(index * last / (MAX_RENDERED_ARCHETYPES - 1))
        for index in range(MAX_RENDERED_ARCHETYPES)
    }
    return [records[index] for index in sorted(indexes)]


def _render_archetype_graph(
    records: list[_ArchetypeRecord], name: str,
) -> tuple[bytes, dict[str, Any]]:
    sampled = _archetype_samples(records)
    dependency_counts: dict[tuple[str, str], int] = {}
    for record in records:
        for dependency in _archetype_dependencies(record):
            dependency_counts[dependency] = dependency_counts.get(dependency, 0) + 1
    sampled_dependencies: dict[tuple[str, str], int] = {}
    for record in sampled:
        for dependency in _archetype_dependencies(record):
            sampled_dependencies[dependency] = dependency_counts[dependency]
    ordered_dependencies = sorted(
        sampled_dependencies,
        key=lambda item: (item[0].casefold(), -sampled_dependencies[item], item[1].casefold()),
    )[:16]
    dependency_y = {
        dependency: 96 + (index * (490 / max(1, len(ordered_dependencies) - 1)))
        for index, dependency in enumerate(ordered_dependencies)
    }
    width, height = 1120, 680
    image = Image.new("RGB", (width, height), "#101714")
    draw = ImageDraw.Draw(image)
    draw.text((42, 22), f"YTYP ARCHETYPE DEPENDENCIES  |  {name[:62]}", fill="#E8F2EC")
    draw.text((42, 53), "ARCHETYPE", fill="#91AA9D")
    draw.text((358, 53), "ASSET BINDING", fill="#91AA9D")
    draw.text((752, 53), "SHARED DICTIONARIES", fill="#91AA9D")
    if len(sampled) == 1:
        row_positions = [320.0]
    else:
        row_positions = [
            88 + (index * (510 / (len(sampled) - 1)))
            for index in range(len(sampled))
        ]
    dependency_colours = {
        "Texture dictionary": "#7CCDA8",
        "Drawable dictionary": "#9B87DB",
        "Physics dictionary": "#E09B6B",
        "Clip dictionary": "#70A9D7",
    }
    for record, y in zip(sampled, row_positions):
        for dependency in _archetype_dependencies(record):
            target_y = dependency_y.get(dependency)
            if target_y is not None:
                draw.line((630, y + 11, 744, target_y + 11), fill="#3D554A", width=1)
        draw.line((292, y + 11, 350, y + 11), fill="#5C7C6C", width=2)
    for dependency, y in dependency_y.items():
        kind, value = dependency
        colour = dependency_colours.get(kind, "#A8BDB2")
        draw.rounded_rectangle((744, y, 1080, y + 22), radius=4,
                               fill="#17231E", outline=colour)
        draw.text((752, y + 4), kind[:19], fill=colour)
        draw.text((880, y + 4), value[:24], fill="#D8E5DE")
        draw.text((1044, y + 4), str(dependency_counts[dependency]), fill="#91AA9D")
    for record, y in zip(sampled, row_positions):
        kind_colour = "#B69CE6" if "Time" in record.kind else "#65C993"
        draw.rounded_rectangle((42, y, 292, y + 22), radius=4,
                               fill="#17231E", outline=kind_colour)
        draw.text((50, y + 4), record.name[:27], fill="#E1ECE6")
        draw.rounded_rectangle((350, y, 630, y + 22), radius=4,
                               fill="#171D24", outline="#6A8CB2")
        draw.text((358, y + 4), record.asset_type.removeprefix("ASSET_TYPE_")[:16],
                  fill="#88AED4")
        draw.text((468, y + 4), record.asset_name[:19], fill="#D8E5DE")
    if len(records) > len(sampled):
        draw.text(
            (42, 620),
            f"Showing {len(sampled):,} evenly sampled archetypes; all {len(records):,} "
            "are counted in the report.", fill="#91AA9D",
        )
    type_counts: dict[str, int] = {}
    asset_type_counts: dict[str, int] = {}
    for record in records:
        type_counts[record.kind] = type_counts.get(record.kind, 0) + 1
        asset_type_counts[record.asset_type] = asset_type_counts.get(record.asset_type, 0) + 1
    draw.text(
        (42, height - 25),
        f"{len(records):,} archetypes  |  {len(type_counts):,} definition types  |  "
        f"{len(dependency_counts):,} unique dictionary dependencies  |  diagnostic graph",
        fill="#AFC5B9",
    )
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    dependency_kinds: dict[str, set[str]] = {}
    for (kind, value) in dependency_counts:
        dependency_kinds.setdefault(kind, set()).add(value)
    return output.getvalue(), {
        "archetype_count": len(records),
        "archetype_definition_types": ", ".join(
            f"{kind}: {count}" for kind, count in sorted(type_counts.items())
        ),
        "archetype_asset_types": ", ".join(
            f"{kind}: {count}" for kind, count in sorted(asset_type_counts.items())
        ),
        "archetype_unique_assets": len({record.asset_name for record in records}),
        "archetype_texture_dictionaries": len(
            dependency_kinds.get("Texture dictionary", set())
        ),
        "archetype_drawable_dictionaries": len(
            dependency_kinds.get("Drawable dictionary", set())
        ),
        "archetype_physics_dictionaries": len(
            dependency_kinds.get("Physics dictionary", set())
        ),
        "archetype_clip_dictionaries": len(
            dependency_kinds.get("Clip dictionary", set())
        ),
        "archetype_extension_count": sum(record.extension_count for record in records),
        "archetype_lod_range": (
            f"{min(record.lod_distance for record in records):.4g} .. "
            f"{max(record.lod_distance for record in records):.4g}"
        ),
        "archetype_preview": "typed asset and dictionary dependency graph",
    }


def _archetype_preview_from_xml(
    xml: Path, name: str,
) -> tuple[bytes | None, dict[str, Any], str | None]:
    """Render a bounded CodeWalker YTYP asset dependency overview."""
    try:
        root = _safe_codewalker_xml(xml).getroot()
        records = _archetype_records(root)
        root_name = _child_text(root, "name")
        metadata: dict[str, Any] = {
            "archetype_dictionary_name": root_name,
            "archetype_declared_dependencies": len(_item_children(root, "dependencies")),
            "archetype_composite_entity_types": len(
                _item_children(root, "compositeEntityTypes")
            ),
        }
        if not records:
            metadata.update({
                "archetype_count": 0,
                "archetype_preview": "No archetype definitions were found",
            })
            return None, metadata, None
        image, rendered = _render_archetype_graph(records, name)
        metadata.update(rendered)
        return image, metadata, None
    except (OSError, ValueError, etree.XMLSyntaxError, OverflowError) as exc:
        return None, {}, f"Archetype preview unavailable: {exc}"


class NativeAssetInspector:
    """Describe native files and optionally invoke CodeWalker XML conversion."""

    def __init__(
        self, project_root: str | Path, gta_path: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.gta_path = (
            Path(gta_path).expanduser().resolve() if gta_path is not None else None
        )
        self.patcher = self.project_root / "tools" / "RpfPatcher" / "RpfPatcher.exe"

    def _asset_xml_args(
        self, source: Path, xml: Path, assets: Path, edition: str,
    ) -> list[str | Path]:
        args: list[str | Path] = [
            self.patcher, "asset-xml", source, xml, assets,
            "gen9" if edition.casefold() == "enhanced" else "legacy",
        ]
        if self.gta_path is not None:
            args.append(self.gta_path)
        return args

    def _asset_from_xml_args(
        self, xml: Path, output: Path, assets: Path, edition: str,
        source: Path,
    ) -> list[str | Path]:
        args: list[str | Path] = [
            self.patcher, "asset-from-xml", xml, output, assets,
            "gen9" if edition.casefold() == "enhanced" else "legacy", source,
        ]
        if self.gta_path is not None:
            args.append(self.gta_path)
        return args

    def inspect_bytes(
        self, name: str, data: bytes, *, edition: str = "Enhanced",
        truncated: bool = False,
    ) -> NativeAssetReport:
        suffix = Path(name).suffix.casefold()
        format_name, metadata = _format_identity(name, data)
        warnings: list[str] = []
        structured: str | None = None
        image_png: bytes | None = None
        model_scene: NativeModelScene | None = None
        if truncated:
            warnings.append("Deep preview skipped because the asset exceeded the safety limit.")
        if suffix == ".gxt2" and not truncated:
            structured, gxt_metadata, gxt_warnings = _gxt2_text(data)
            metadata.update(gxt_metadata)
            warnings.extend(gxt_warnings)
        if (not truncated and suffix in NATIVE_XML_SUFFIXES
                and self.patcher.is_file()):
            converted = self._convert(name, data, edition)
            if (
                converted is not None and converted.conversion_error
                and suffix not in AUDIO_PREVIEW_SUFFIXES
            ):
                alternate = "Legacy" if edition.casefold() == "enhanced" else "Enhanced"
                retried = self._convert(name, data, alternate)
                if retried is not None and not retried.conversion_error:
                    converted = retried
                    metadata["interpreted_edition"] = alternate
                    warnings.append(
                        f"Requested {edition} decoding failed; the resource parsed as {alternate}."
                    )
            if converted is not None:
                structured = converted.structured_text
                image_png = converted.image_png
                model_scene = converted.model_scene
                metadata.update(converted.metadata)
                if converted.texture_count:
                    metadata["exported_textures"] = converted.texture_count
                if converted.conversion_error:
                    warnings.append(converted.conversion_error)
        elif suffix in NATIVE_XML_SUFFIXES and not self.patcher.is_file():
            warnings.append(
                "RpfPatcher is not built; run runtools.ps1 for structured CodeWalker preview."
            )
        return NativeAssetReport(
            name=name, suffix=suffix, format_name=format_name, size=len(data),
            sha256=hashlib.sha256(data).hexdigest(), metadata=metadata,
            structured_text=structured, image_png=image_png,
            warnings=tuple(warnings), model_scene=model_scene,
        )

    def _convert(
        self, name: str, data: bytes, edition: str,
    ) -> _ConvertedAsset | None:
        with tempfile.TemporaryDirectory(prefix="allin1-native-asset-") as temporary:
            root = Path(temporary)
            safe_name = Path(name).name or f"asset{Path(name).suffix}"
            source = root / safe_name
            xml = root / f"{safe_name}.xml"
            assets = root / "assets"
            source.write_bytes(data)
            completed = run_hidden(
                self._asset_xml_args(source, xml, assets, edition),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if completed.returncode or not xml.is_file():
                detail = (completed.stderr or completed.stdout or "conversion failed").strip()
                return None if not detail else _ConvertedAsset(
                    "", None, 0,
                    conversion_error=f"CodeWalker preview failed: {detail}",
                )
            xml_size = xml.stat().st_size
            preview_metadata: dict[str, Any] = {}
            geometry_image: bytes | None = None
            model_scene: NativeModelScene | None = None
            suffix = Path(name).suffix.casefold()
            if suffix in MODEL_PREVIEW_SUFFIXES:
                model_scene, preview_metadata, preview_warning = _model_scene_from_xml(
                    xml, Path(name).name,
                )
                if model_scene is not None:
                    geometry_image, preview_metadata = model_scene.render()
                if preview_warning:
                    preview_metadata["model_preview"] = preview_warning
            elif suffix in COLLISION_PREVIEW_SUFFIXES:
                geometry_image, preview_metadata, preview_warning = _collision_preview_from_xml(
                    xml, Path(name).name,
                )
                if preview_warning:
                    preview_metadata["collision_preview"] = preview_warning
            elif suffix in MAP_PREVIEW_SUFFIXES:
                geometry_image, preview_metadata, preview_warning = _map_preview_from_xml(
                    xml, Path(name).name,
                )
                if preview_warning:
                    preview_metadata["map_preview"] = preview_warning
            elif suffix in NAVMESH_PREVIEW_SUFFIXES:
                geometry_image, preview_metadata, preview_warning = _navmesh_preview_from_xml(
                    xml, Path(name).name,
                )
                if preview_warning:
                    preview_metadata["navmesh_preview"] = preview_warning
            elif suffix in PATH_PREVIEW_SUFFIXES:
                geometry_image, preview_metadata, preview_warning = _path_preview_from_xml(
                    xml, Path(name).name,
                )
                if preview_warning:
                    preview_metadata["path_preview"] = preview_warning
            elif suffix in ARCHETYPE_PREVIEW_SUFFIXES:
                geometry_image, preview_metadata, preview_warning = _archetype_preview_from_xml(
                    xml, Path(name).name,
                )
                if preview_warning:
                    preview_metadata["archetype_preview"] = preview_warning
            elif suffix in AUDIO_PREVIEW_SUFFIXES:
                preview_metadata, preview_warning = _awc_preview_from_xml(xml, assets)
                if preview_warning:
                    preview_metadata["audio_preview_warning"] = preview_warning
            with xml.open("r", encoding="utf-8", errors="replace") as stream:
                text = stream.read(2_000_000)
            if xml_size > 2_000_000:
                text += (
                    f"\n\n<!-- Preview truncated at 2,000,000 characters; "
                    f"full CodeWalker XML was {xml_size:,} bytes. -->\n"
                )
            texture_image, count = _texture_contact_sheet(assets)
            return _ConvertedAsset(
                text, geometry_image or texture_image, count,
                metadata=preview_metadata, model_scene=model_scene,
            )

    def export_workspace(
        self, source: str | Path, destination: str | Path, *, edition: str,
    ) -> Path:
        authored = Path(source).expanduser()
        if authored.is_symlink():
            raise ValueError("Native source asset cannot be a symbolic link")
        resolved = authored.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Native source asset not found: {resolved}")
        if resolved.stat().st_size > MAX_NATIVE_PREVIEW_BYTES:
            raise ValueError("Native source asset exceeds the guarded workspace limit")
        return self.export_workspace_bytes(
            resolved.name, resolved.read_bytes(), destination, edition=edition,
            source_path=resolved,
        )

    def export_workspace_bytes(
        self, name: str, data: bytes, destination: str | Path, *, edition: str,
        source_path: Path | None = None,
    ) -> Path:
        """Export XML, dependencies, and an immutable source snapshot for editing."""
        self._require_patcher()
        safe_name = Path(name).name
        suffix = Path(safe_name).suffix.casefold()
        if not safe_name or suffix not in NATIVE_XML_IMPORT_SUFFIXES:
            raise ValueError(f"Native XML round-trip is not supported for {name}")
        if not data or len(data) > MAX_NATIVE_PREVIEW_BYTES:
            raise ValueError("Native workspace source is empty or exceeds the guarded limit")
        normalized_edition = self._normalize_edition(edition)
        target = Path(destination).expanduser().resolve()
        if target.exists() or target.is_symlink():
            raise ValueError(f"Native workspace destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(
            prefix=f".{target.name}.allin1-stage-", dir=target.parent,
        )).resolve()
        try:
            original_dir = staging / "original"
            edit_dir = staging / "edit"
            assets = edit_dir / "assets"
            original_dir.mkdir()
            assets.mkdir(parents=True)
            source_snapshot = original_dir / safe_name
            source_snapshot.write_bytes(data)
            xml = edit_dir / f"{safe_name}.xml"
            completed = run_hidden(
                self._asset_xml_args(source_snapshot, xml, assets, normalized_edition),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if completed.returncode or not xml.is_file():
                detail = (
                    completed.stderr or completed.stdout or "conversion failed"
                ).strip()
                raise RuntimeError(f"Native XML workspace export failed: {detail}")
            dependencies = self._workspace_files(assets)
            manifest = {
                "schema_version": NATIVE_WORKSPACE_SCHEMA,
                "operation": "native_asset_workspace",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "edition": normalized_edition,
                "source": {
                    "name": safe_name, "suffix": suffix, "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "snapshot": f"original/{safe_name}",
                    "authored_path": str(source_path) if source_path else None,
                },
                "xml": {
                    "path": f"edit/{safe_name}.xml", "size": xml.stat().st_size,
                    "base_sha256": _sha256_file(xml),
                },
                "dependencies": dependencies,
                "safety": {
                    "source_snapshot_immutable": True,
                    "game_write_performed": False,
                    "rebuilt_asset_requires_parse_validation": True,
                    "gta_installation_keys_required": suffix == ".awc",
                    "gta_installation_path_stored": False,
                },
            }
            _write_json_atomic(staging / "native-workspace.json", manifest)
            staging.replace(target)
            return target
        except Exception:
            if staging.is_dir() and staging.parent == target.parent:
                shutil.rmtree(staging)
            raise

    def build_workspace(
        self, workspace: str | Path, output: str | Path,
    ) -> tuple[Path, Path]:
        """Rebuild and reparse one manifest-backed native editing workspace."""
        self._require_patcher()
        authored_root = Path(workspace).expanduser()
        if authored_root.is_symlink():
            raise ValueError("Native workspace cannot be a symbolic link")
        root = authored_root.resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Native workspace not found: {root}")
        manifest_path = root / "native-workspace.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise ValueError("Native workspace manifest is missing or unsafe")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid native workspace manifest: {exc}") from exc
        if not isinstance(manifest, dict) or manifest.get(
            "schema_version"
        ) != NATIVE_WORKSPACE_SCHEMA or manifest.get(
            "operation"
        ) != "native_asset_workspace":
            raise ValueError("Unsupported native workspace manifest")
        source = manifest.get("source")
        xml_meta = manifest.get("xml")
        if not isinstance(source, dict) or not isinstance(xml_meta, dict):
            raise ValueError("Native workspace is missing source or XML metadata")
        source_snapshot = self._workspace_member(root, source.get("snapshot"), "source")
        xml = self._workspace_member(root, xml_meta.get("path"), "XML")
        assets = (root / "edit" / "assets").resolve()
        if not assets.is_relative_to(root) or not assets.is_dir() or assets.is_symlink():
            raise ValueError("Native workspace asset folder is missing or unsafe")
        if not source_snapshot.is_file() or source_snapshot.is_symlink():
            raise ValueError("Native workspace source snapshot is missing or unsafe")
        if not xml.is_file() or xml.is_symlink():
            raise ValueError("Native workspace XML is missing or unsafe")
        if not 0 < xml.stat().st_size <= MAX_NATIVE_WORKSPACE_BYTES:
            raise ValueError("Native workspace XML is empty or exceeds guarded limits")
        source_name = source.get("name")
        expected_suffix = str(source.get("suffix", "")).casefold()
        if (
            not isinstance(source_name, str) or not source_name
            or Path(source_name).name != source_name
            or source_snapshot.name != source_name
            or Path(source_name).suffix.casefold() != expected_suffix
        ):
            raise ValueError("Native workspace source identity was modified")
        source_size = source.get("size")
        source_sha256 = source.get("sha256")
        if (
            not isinstance(source_size, int) or isinstance(source_size, bool)
            or not isinstance(source_sha256, str) or len(source_sha256) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in source_sha256)
        ):
            raise ValueError("Native workspace source metadata is malformed")
        normalized_source_sha256 = source_sha256.casefold()
        if source_snapshot.stat().st_size != source_size or _sha256_file(
            source_snapshot
        ) != normalized_source_sha256:
            raise ValueError("Native workspace source snapshot was modified")
        if expected_suffix not in NATIVE_XML_IMPORT_SUFFIXES:
            raise ValueError("Native workspace has an unsupported output format")
        edition = self._normalize_edition(str(manifest.get("edition", "")))
        self._workspace_files(assets)

        destination = Path(output).expanduser().resolve()
        if destination.suffix.casefold() != expected_suffix:
            raise ValueError(
                f"Native workspace output must retain the {expected_suffix} extension"
            )
        report = destination.with_name(f"{destination.name}.allin1.json")
        if (
            destination.exists() or destination.is_symlink()
            or report.exists() or report.is_symlink()
        ):
            raise ValueError(f"Native workspace output already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="allin1-native-build-", dir=destination.parent,
        ) as temporary:
            stage_root = Path(temporary)
            staged = stage_root / destination.name
            completed = run_hidden(
                self._asset_from_xml_args(
                    xml, staged, assets, edition, source_snapshot,
                ),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if completed.returncode or not staged.is_file():
                detail = (
                    completed.stderr or completed.stdout or "rebuild failed"
                ).strip()
                raise RuntimeError(f"Native XML workspace rebuild failed: {detail}")
            if not 0 < staged.stat().st_size <= MAX_NATIVE_PREVIEW_BYTES:
                raise RuntimeError("Rebuilt native asset is empty or exceeds the safe limit")
            validation_xml = stage_root / f"{destination.name}.validated.xml"
            validation_assets = stage_root / "validated-assets"
            validation = run_hidden(
                self._asset_xml_args(
                    staged, validation_xml, validation_assets, edition,
                ),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if validation.returncode or not validation_xml.is_file():
                detail = (
                    validation.stderr or validation.stdout or "parse validation failed"
                ).strip()
                raise RuntimeError(f"Rebuilt native asset failed validation: {detail}")
            edited_semantic_hash = _semantic_xml_sha256(xml)
            validation_semantic_hash = _semantic_xml_sha256(validation_xml)
            semantic_match = edited_semantic_hash == validation_semantic_hash
            if expected_suffix == ".awc" and not semantic_match:
                raise RuntimeError(
                    "Rebuilt AWC reparsed but its structured stream definition changed"
                )
            result = {
                "schema_version": 1,
                "operation": "native_asset_workspace_build",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "workspace": str(root), "edition": edition,
                "source_sha256": normalized_source_sha256,
                "edited_xml_sha256": _sha256_file(xml),
                "output": {
                    "path": str(destination), "size": staged.stat().st_size,
                    "sha256": _sha256_file(staged),
                },
                "validation": {
                    "reparsed": True,
                    "xml_sha256": _sha256_file(validation_xml),
                    "edited_semantic_xml_sha256": edited_semantic_hash,
                    "reparsed_semantic_xml_sha256": validation_semantic_hash,
                    "semantic_xml_match": semantic_match,
                    "dependency_count": len(self._workspace_files(validation_assets)),
                },
            }
            temporary_report = stage_root / "build-report.json"
            _write_json_atomic(temporary_report, result)
            published: list[Path] = []
            try:
                # Keep both publications inside the destination filesystem. On the
                # Windows desktop target rename refuses a raced destination; cleanup
                # below also prevents a half-published asset/report pair.
                temporary_report.rename(report)
                published.append(report)
                staged.rename(destination)
                published.append(destination)
            except Exception:
                for path in reversed(published):
                    try:
                        path.unlink()
                    except OSError:
                        pass
                raise
        return destination, report

    def _require_patcher(self) -> None:
        if not self.patcher.is_file():
            raise FileNotFoundError(
                "RpfPatcher is not built; run runtools.ps1 before native authoring"
            )

    @staticmethod
    def _normalize_edition(edition: str) -> str:
        normalized = str(edition).strip().casefold()
        if normalized in {"enhanced", "gen9"}:
            return "Enhanced"
        if normalized == "legacy":
            return "Legacy"
        raise ValueError("Native workspace edition must be Legacy or Enhanced")

    @staticmethod
    def _workspace_member(root: Path, value: object, label: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Native workspace {label} path is missing")
        relative = PurePosixPath(value.replace("\\", "/"))
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError(f"Native workspace {label} path is unsafe")
        resolved = root.joinpath(*relative.parts).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Native workspace {label} path escapes its root")
        return resolved

    @staticmethod
    def _workspace_files(root: Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        total = 0
        if not root.is_dir():
            return records
        for path in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
            if path.is_symlink():
                raise ValueError(f"Native workspace contains a symbolic link: {path}")
            resolved = path.resolve()
            if not resolved.is_relative_to(root.resolve()):
                raise ValueError(f"Native workspace dependency escapes its root: {path}")
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            size = path.stat().st_size
            total += size
            records.append({
                "path": relative, "size": size, "sha256": _sha256_file(path),
            })
            if len(records) > MAX_NATIVE_WORKSPACE_FILES or total > (
                MAX_NATIVE_WORKSPACE_BYTES
            ):
                raise ValueError("Native workspace dependencies exceed guarded limits")
        return records


def native_preview_limit(name: str, size: int) -> int:
    """Return the bounded read size appropriate for a package member."""
    if Path(name).suffix.casefold() not in NATIVE_ASSET_SUFFIXES:
        return min(size + 1, 8 * 1024 * 1024)
    return min(size + 1, MAX_NATIVE_PREVIEW_BYTES)
