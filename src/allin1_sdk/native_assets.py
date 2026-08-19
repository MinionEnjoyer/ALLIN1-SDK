"""Inspection and guarded XML round-tripping of native GTA V assets.

The lightweight parsers in this module always work and intentionally stop at
well-defined headers.  When the pinned RpfPatcher/CodeWalker helper is present,
supported RAGE resources are additionally converted to their structured XML
representation and texture dictionaries receive a visual contact sheet. Supported
resources can also be exported into snapshot-backed editing workspaces and rebuilt
only after the result successfully reparses through CodeWalker.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import shutil
import struct
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from lxml import etree
from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from allin1_sdk.gxt2_workspace import Gxt2Workspace
from allin1_sdk.processes import run_hidden


NATIVE_XML_SUFFIXES = frozenset({
    ".awc", ".gxt2", ".rel", ".ybn", ".ycd", ".ydd", ".ydr",
    ".yed", ".yfd", ".yft", ".ymap", ".ymf", ".ymt", ".ynd",
    ".ynv", ".ypt", ".ytd", ".ytyp", ".yvr", ".ywr",
})
NATIVE_XML_IMPORT_SUFFIXES = frozenset({
    ".awc", ".rel", ".ybn", ".ycd", ".ydd", ".ydr", ".yed",
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
MAX_MODEL_XML_BYTES = 192 * 1024 * 1024
MAX_MODEL_VERTICES = 1_000_000
MAX_MODEL_TRIANGLES = 1_000_000
MAX_RENDERED_TRIANGLES = 45_000
MAX_MAP_ENTITIES = 250_000
MAX_RENDERED_MAP_ENTITIES = 80_000


@dataclass(frozen=True)
class _ConvertedAsset:
    structured_text: str
    image_png: bytes | None
    texture_count: int
    metadata: dict[str, Any] = field(default_factory=dict)
    conversion_error: str | None = None


@dataclass(frozen=True)
class _ModelGeometry:
    vertices: tuple[tuple[float, float, float], ...]
    triangles: tuple[tuple[int, int, int], ...]
    lod: str


@dataclass(frozen=True)
class _MapEntity:
    archetype: str
    position: tuple[float, float, float]
    parent: int
    yaw: float
    scale: float


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
        ".ymt": "Rockstar metadata resource", ".gxt2": "Rockstar GXT2 text table",
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


def _model_lod(vertex_buffer: etree._Element) -> str:
    parent = vertex_buffer.getparent()
    while parent is not None:
        name = _local_name(parent)
        if name.startswith("DrawableModels"):
            return name.removeprefix("DrawableModels") or "Unknown"
        parent = parent.getparent()
    return "Default"


def _read_model_geometry(
    vertex_buffer: etree._Element,
) -> _ModelGeometry | None:
    layout = vertex_buffer.find("./Layout")
    data = vertex_buffer.find("./Data")
    offset = _model_position_offset(layout)
    if data is None or not data.text or offset is None:
        return None
    vertices: list[tuple[float, float, float]] = []
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
        if len(vertices) > MAX_MODEL_VERTICES:
            raise ValueError("Model preview exceeds the guarded vertex limit")
    if not vertices:
        return None

    geometry = vertex_buffer.getparent()
    index_data = None if geometry is None else geometry.find("./IndexBuffer/Data")
    if index_data is None or not index_data.text:
        return _ModelGeometry(tuple(vertices), (), _model_lod(vertex_buffer))
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
    return _ModelGeometry(tuple(vertices), tuple(triangles), _model_lod(vertex_buffer))


def _model_drawable_count(root: etree._Element) -> int:
    name = _local_name(root)
    if name == "DrawableDictionary":
        return len(root.xpath("./*[local-name()='Item']"))
    if name in {"Drawable", "Fragment"}:
        return 1
    count = len(root.xpath(".//*[local-name()='Drawable']"))
    return count or 1


def _project_model_point(
    point: tuple[float, float, float],
    center: tuple[float, float, float],
) -> tuple[float, float, float]:
    x, y, z = (point[index] - center[index] for index in range(3))
    yaw = math.radians(34.0)
    pitch = math.radians(24.0)
    rotated_x = (x * math.cos(yaw)) - (y * math.sin(yaw))
    rotated_y = (x * math.sin(yaw)) + (y * math.cos(yaw))
    screen_y = (z * math.cos(pitch)) - (rotated_y * math.sin(pitch))
    depth = (rotated_y * math.cos(pitch)) + (z * math.sin(pitch))
    return rotated_x, screen_y, depth


def _render_model_wireframe(
    geometries: list[_ModelGeometry], name: str, *,
    title: str = "MODEL PREVIEW", geometry_label: str = "geometries",
) -> tuple[bytes, dict[str, Any]]:
    vertices = [point for geometry in geometries for point in geometry.vertices]
    minima = tuple(min(point[axis] for point in vertices) for axis in range(3))
    maxima = tuple(max(point[axis] for point in vertices) for axis in range(3))
    center = tuple((minima[axis] + maxima[axis]) / 2.0 for axis in range(3))
    projected = [_project_model_point(point, center) for point in vertices]
    min_x = min(point[0] for point in projected)
    max_x = max(point[0] for point in projected)
    min_y = min(point[1] for point in projected)
    max_y = max(point[1] for point in projected)
    width, height = 960, 680
    view_left, view_top, view_right, view_bottom = 38, 76, width - 38, height - 56
    span_x = max(max_x - min_x, 1e-9)
    span_y = max(max_y - min_y, 1e-9)
    scale = min((view_right - view_left) / span_x, (view_bottom - view_top) / span_y)
    center_x = (view_left + view_right) / 2.0
    center_y = (view_top + view_bottom) / 2.0

    def screen(point: tuple[float, float, float]) -> tuple[float, float, float]:
        px, py, depth = _project_model_point(point, center)
        return center_x + (px * scale), center_y - (py * scale), depth

    total_triangles = sum(len(geometry.triangles) for geometry in geometries)
    rendered: list[tuple[float, int, tuple[tuple[float, float], ...], float]] = []
    for geometry_index, geometry in enumerate(geometries):
        triangle_count = len(geometry.triangles)
        if not triangle_count:
            continue
        quota = max(1, round(MAX_RENDERED_TRIANGLES * triangle_count / total_triangles))
        stride = max(1, math.ceil(triangle_count / quota))
        for triangle in geometry.triangles[::stride]:
            raw = [geometry.vertices[index] for index in triangle]
            transformed = [screen(point) for point in raw]
            ax, ay, az = (raw[1][i] - raw[0][i] for i in range(3))
            bx, by, bz = (raw[2][i] - raw[0][i] for i in range(3))
            nx, ny, nz = (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)
            magnitude = math.sqrt(nx * nx + ny * ny + nz * nz)
            light = 0.3 if magnitude <= 1e-12 else abs(
                ((nx * 0.32) + (ny * -0.42) + (nz * 0.85)) / magnitude
            )
            rendered.append((
                sum(point[2] for point in transformed) / 3.0,
                geometry_index,
                tuple((point[0], point[1]) for point in transformed),
                min(1.0, max(0.16, light)),
            ))
    rendered.sort(key=lambda item: item[0])

    image = Image.new("RGB", (width, height), "#101714")
    draw = ImageDraw.Draw(image)
    for y in range(view_top, view_bottom + 1, 48):
        draw.line((view_left, y, view_right, y), fill="#18231e", width=1)
    for x in range(view_left, view_right + 1, 48):
        draw.line((x, view_top, x, view_bottom), fill="#18231e", width=1)
    draw.rectangle((view_left, view_top, view_right, view_bottom), outline="#2d4036")
    for _depth, geometry_index, polygon, light in rendered:
        accent = (geometry_index * 31) % 45
        fill = (
            int(17 + (22 * light)),
            int(48 + accent + (70 * light)),
            int(39 + (50 * light)),
        )
        outline = (
            int(42 + (38 * light)),
            int(112 + (92 * light)),
            int(76 + (68 * light)),
        )
        draw.polygon(polygon, fill=fill, outline=outline)
    if not rendered:
        for geometry in geometries:
            points = [screen(point) for point in geometry.vertices]
            for point in points[::max(1, len(points) // 12_000)]:
                x, y, _depth = point
                draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill="#5bd995")
    draw.text((38, 24), f"{title}  |  {name[:72]}", fill="#E8F2EC")
    draw.text(
        (38, height - 32),
        f"{len(vertices):,} vertices  |  {total_triangles:,} triangles  |  "
        f"{len(geometries):,} {geometry_label}  |  isometric diagnostic view",
        fill="#AFC5B9",
    )
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    lods: dict[str, int] = {}
    for geometry in geometries:
        lods[geometry.lod] = lods.get(geometry.lod, 0) + 1
    return output.getvalue(), {
        "model_geometry_count": len(geometries),
        "model_vertex_count": len(vertices),
        "model_triangle_count": total_triangles,
        "model_lods": ", ".join(f"{name}: {count}" for name, count in sorted(lods.items())),
        "model_bounds": " x ".join(
            f"{maxima[axis] - minima[axis]:.4g}" for axis in range(3)
        ),
        "model_preview": "isometric geometry diagnostic",
    }


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


def _model_preview_from_xml(
    xml: Path, name: str,
) -> tuple[bytes | None, dict[str, Any], str | None]:
    """Build a bounded diagnostic preview from CodeWalker model XML."""
    try:
        tree = _safe_codewalker_xml(xml)
        root = tree.getroot()
        geometries: list[_ModelGeometry] = []
        total_vertices = 0
        total_triangles = 0
        skipped_layouts = 0
        for vertex_buffer in root.xpath(".//*[local-name()='VertexBuffer']"):
            geometry = _read_model_geometry(vertex_buffer)
            if geometry is None:
                skipped_layouts += 1
                continue
            total_vertices += len(geometry.vertices)
            total_triangles += len(geometry.triangles)
            if total_vertices > MAX_MODEL_VERTICES:
                raise ValueError("Model preview exceeds the guarded vertex limit")
            if total_triangles > MAX_MODEL_TRIANGLES:
                raise ValueError("Model preview exceeds the guarded triangle limit")
            geometries.append(geometry)
        if not geometries:
            return None, {
                "model_drawable_count": _model_drawable_count(root),
                "model_preview": "No supported position buffers were found",
            }, None
        image, metadata = _render_model_wireframe(geometries, name)
        metadata["model_drawable_count"] = _model_drawable_count(root)
        if skipped_layouts:
            metadata["model_skipped_buffers"] = skipped_layouts
        return image, metadata, None
    except (OSError, ValueError, etree.XMLSyntaxError, OverflowError) as exc:
        return None, {}, f"Model preview unavailable: {exc}"


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


class NativeAssetInspector:
    """Describe native files and optionally invoke CodeWalker XML conversion."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.patcher = self.project_root / "tools" / "RpfPatcher" / "RpfPatcher.exe"

    def inspect_bytes(
        self, name: str, data: bytes, *, edition: str = "Enhanced",
        truncated: bool = False,
    ) -> NativeAssetReport:
        suffix = Path(name).suffix.casefold()
        format_name, metadata = _format_identity(name, data)
        warnings: list[str] = []
        structured: str | None = None
        image_png: bytes | None = None
        if truncated:
            warnings.append("Deep preview skipped because the asset exceeded the safety limit.")
        if suffix == ".gxt2" and not truncated:
            structured, gxt_metadata, gxt_warnings = _gxt2_text(data)
            metadata.update(gxt_metadata)
            warnings.extend(gxt_warnings)
        if (not truncated and suffix in NATIVE_XML_SUFFIXES
                and self.patcher.is_file()):
            converted = self._convert(name, data, edition)
            if converted is not None and converted.conversion_error:
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
            warnings=tuple(warnings),
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
                [
                    self.patcher, "asset-xml", source, xml, assets,
                    "gen9" if edition.casefold() == "enhanced" else "legacy",
                ],
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
            suffix = Path(name).suffix.casefold()
            if suffix in MODEL_PREVIEW_SUFFIXES:
                geometry_image, preview_metadata, preview_warning = _model_preview_from_xml(
                    xml, Path(name).name,
                )
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
                metadata=preview_metadata,
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
                [
                    self.patcher, "asset-xml", source_snapshot, xml, assets,
                    "gen9" if normalized_edition == "Enhanced" else "legacy",
                ],
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
        if source_snapshot.stat().st_size != source_size or _sha256_file(
            source_snapshot
        ) != source_sha256:
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
                [
                    self.patcher, "asset-from-xml", xml, staged, assets,
                    "gen9" if edition == "Enhanced" else "legacy", source_snapshot,
                ],
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
                [
                    self.patcher, "asset-xml", staged, validation_xml,
                    validation_assets,
                    "gen9" if edition == "Enhanced" else "legacy",
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if validation.returncode or not validation_xml.is_file():
                detail = (
                    validation.stderr or validation.stdout or "parse validation failed"
                ).strip()
                raise RuntimeError(f"Rebuilt native asset failed validation: {detail}")
            result = {
                "schema_version": 1,
                "operation": "native_asset_workspace_build",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "workspace": str(root), "edition": edition,
                "source_sha256": source["sha256"],
                "edited_xml_sha256": _sha256_file(xml),
                "output": {
                    "path": str(destination), "size": staged.stat().st_size,
                    "sha256": _sha256_file(staged),
                },
                "validation": {
                    "reparsed": True,
                    "xml_sha256": _sha256_file(validation_xml),
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
