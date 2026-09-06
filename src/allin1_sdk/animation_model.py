"""Read-only, explicit drawable/skeleton binding for the animation viewport.

CodeWalker XML uses bone tags for animation and geometry-local palette indices
for skinning. Never infer a skeleton from a different dictionary item. XML bind
transforms are reconstructed; this is not a claim about retail inverse-bind data.
"""
from __future__ import annotations

import hashlib
import math

from lxml import etree

from allin1_sdk.native_assets import _read_model_geometry, _model_bone_transform_values
from allin1_sdk.workspace_desktop import path

MAX_XML = 16 * 1024**2
MAX_VERTICES = 30000
MAX_TRIANGLES = 40000
LODS = ("High", "Medium", "Low", "VeryLow")


def _integer(node, field, minimum, maximum):
    child = node.find(field)
    try:
        value = int(child.get("value", "") if child is not None else "", 10)
    except ValueError as exc:
        raise ValueError(f"Missing or invalid {field}") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{field} is outside the supported range")
    return value


def _chunks(values):
    # The desktop report broker limits each array to 2,000 entries.
    return [values[i:i + 1500] for i in range(0, len(values), 1500)]


def _read_xml(source):
    selected = path(source)
    if selected.suffix.casefold() != ".xml" or not selected.is_file():
        raise ValueError("Choose an exported YDR/YDD/YFT model XML file")
    if selected.stat().st_size > MAX_XML:
        raise ValueError("Animation model XML exceeds 16 MiB")
    with selected.open("rb") as stream:
        data = stream.read(MAX_XML + 1)
    return selected, data


def inspect(source, drawable=None, lod=None, *, skeleton_xml=None, skeleton_drawable=None):
    selected, data = _read_xml(source)
    skeleton_path, skeleton_data = _read_xml(skeleton_xml) if skeleton_xml is not None else (None, None)
    result = analyze(data, drawable, lod, skeleton_data=skeleton_data, skeleton_drawable=skeleton_drawable)
    result["source"] = str(selected)
    if skeleton_path:
        result["skeleton_binding"]["source"] = str(skeleton_path)
    return result


def _drawables(data):
    if len(data) > MAX_XML:
        raise ValueError("Animation model XML exceeds 16 MiB")
    try:
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
    except etree.XMLSyntaxError as exc:
        raise ValueError("Invalid model XML") from exc
    if root.getroottree().docinfo.doctype:
        raise ValueError("Model XML cannot contain a DTD")
    if root.tag == "Drawable":
        drawables = [root]
    elif root.tag == "DrawableDictionary":
        drawables = root.findall("Item")
    elif root.tag == "Fragment":
        drawables = root.findall("Drawable")
    else:
        raise ValueError("Expected an exported Drawable, DrawableDictionary or Fragment")
    if not 1 <= len(drawables) <= 128:
        raise ValueError("Model must contain 1–128 primary drawables")
    return drawables


def _choices(drawables):
    return [{"key": str(i), "name": (item.findtext("Name") or f"Drawable {i}")[:256]} for i, item in enumerate(drawables)]


def _select(drawables, drawable):
    if drawable is None and len(drawables) == 1:
        drawable = "0"
    if drawable is not None and (not isinstance(drawable, str) or drawable not in {str(i) for i in range(len(drawables))}):
        raise ValueError("Choose an exact drawable from this model")
    return drawable


def compatible_skeleton(owner, skeleton_owner):
    """Validate authored embedded/shared bind agreement without guessing rig intent."""
    nodes = skeleton_owner.findall("Skeleton/Bones/Item")
    if not 1 <= len(nodes) <= 512:
        raise ValueError("Selected skeleton drawable must contain 1–512 bones")
    embedded = owner.findall("Skeleton/Bones/Item")
    if embedded and (len(embedded) > len(nodes) or any(
        any(_integer(a, field, low, high) != _integer(b, field, low, high)
            for field, low, high in (("Index", 0, 511), ("Tag", 0, 65535), ("ParentIndex", -1, 511)))
        or any(not math.isclose(x, y, rel_tol=1e-6, abs_tol=1e-6)
               for xs, ys in zip(_model_bone_transform_values(a), _model_bone_transform_values(b)) for x, y in zip(xs, ys))
        for a, b in zip(embedded, nodes))):
        raise ValueError("Selected shared skeleton conflicts with the model's embedded bind skeleton")
    return nodes


def analyze(data: bytes, drawable=None, lod=None, *, skeleton_data=None, skeleton_drawable=None):
    drawables = _drawables(data)
    drawable = _select(drawables, drawable)
    packet = {"schema_version": 1, "read_only": True, "source_sha256": hashlib.sha256(data).hexdigest(),
              "drawables": _choices(drawables), "selected": drawable, "lod": None, "lods": [], "bones": [], "meshes": [],
              "vertex_count": 0, "triangle_count": 0,
              "scope": "Untextured XML-bind-pose playback. Exact bone tags and geometry palettes; no expression, cloth, physics or game-render equivalence. Fragment physics children are not included."}
    skeleton_owner = None
    if skeleton_data is not None:
        skeleton_owners = _drawables(skeleton_data)
        skeleton_drawable = _select(skeleton_owners, skeleton_drawable)
        packet["skeleton_binding"] = {"mode": "external", "source_sha256": hashlib.sha256(skeleton_data).hexdigest(),
            "drawables": _choices(skeleton_owners), "selected": skeleton_drawable,
            "scope": "User-selected shared skeleton. Index/palette/tag validity is checked, but does not prove this is the model's intended rig."}
        if skeleton_drawable is not None:
            skeleton_owner = skeleton_owners[int(skeleton_drawable)]
    elif skeleton_drawable is not None:
        raise ValueError("Choose a skeleton XML before its drawable")
    if drawable is None:
        return packet
    owner = drawables[int(drawable)]
    packet["lods"] = [name for name in LODS if owner.findall(f"DrawableModels{name}/Item")]
    if lod is None:
        lod = next(iter(packet["lods"]), None)
    if lod not in packet["lods"]:
        raise ValueError("Choose an available model LOD")
    packet["lod"] = lod
    if skeleton_data is not None and skeleton_owner is None:
        packet["binding_required"] = "Choose an exact drawable from the shared skeleton file"
        return packet
    nodes = (skeleton_owner if skeleton_owner is not None else owner).findall("Skeleton/Bones/Item")
    if not nodes and skeleton_owner is None:
        packet["binding_required"] = "This drawable has no skeleton. Choose the matching exported skeleton XML explicitly."
        return packet
    if not 1 <= len(nodes) <= 512:
        raise ValueError("Selected skeleton drawable must contain 1–512 bones")
    if skeleton_owner is not None:
        compatible_skeleton(owner, skeleton_owner)
    tags = set()
    for ordinal, node in enumerate(nodes):
        index = _integer(node, "Index", 0, 511)
        tag = _integer(node, "Tag", 0, 65535)
        parent = _integer(node, "ParentIndex", -1, len(nodes) - 1)
        if index != ordinal or tag in tags:
            raise ValueError("Skeleton indices must match XML array order and bone tags must be unique")
        tags.add(tag)
        translation, rotation, scale = _model_bone_transform_values(node)
        if any(abs(value) > 1e6 for value in (*translation, *scale)) or any(abs(value) < 1e-6 for value in scale):
            raise ValueError("Bone transform is singular or exceeds playback limits")
        if not math.isclose(math.hypot(*rotation), 1, rel_tol=1e-6):
            raise ValueError("Bone quaternion cannot be normalized within numeric limits")
        packet["bones"].append({"index": index, "tag": tag, "parent": parent, "name": (node.findtext("Name") or str(tag))[:256],
                                "translation": list(translation), "rotation": list(rotation), "scale": list(scale)})
    for bone in packet["bones"]:
        seen, current = set(), bone["index"]
        while current != -1:
            if current in seen:
                raise ValueError("Skeleton contains a bone-parent cycle")
            seen.add(current)
            current = packet["bones"][current]["parent"]
    for model in owner.findall(f"DrawableModels{lod}/Item"):
        skin = _integer(model, "HasSkin", 0, 1) == 1
        rigid = _integer(model, "BoneIndex", 0, len(nodes) - 1)
        for geometry in model.findall("Geometries/Item"):
            vb = geometry.find("VertexBuffer")
            if vb is None:
                raise ValueError("Model geometry has no vertex buffer")
            parsed = _read_model_geometry(vb)
            if parsed is None or not parsed.triangles:
                raise ValueError("Model geometry has no supported triangle positions")
            if any(abs(v) > 1e6 for point in parsed.vertices for v in point):
                raise ValueError("Model position exceeds playback limits")
            weights, indices = [], []
            if skin:
                layout = vb.find("Layout")
                if layout is None or layout.get("type") != "GTAV1":
                    raise ValueError("Skinning currently requires a decoded GTAV1 vertex layout")
                offsets, width = {}, 0
                for field in layout:
                    name = field.tag
                    count = 3 if name in ("Position", "Normal", "Binormal") else 2 if isinstance(name, str) and name.startswith("TexCoord") else 4 if name in ("BlendWeights", "BlendIndices", "Colour0", "Colour1", "Tangent") else None
                    if count is None or name in offsets:
                        raise ValueError("Unsupported or duplicate skin vertex semantic")
                    offsets[name] = width
                    width += count
                if "BlendWeights" not in offsets or "BlendIndices" not in offsets:
                    raise ValueError("Skinned geometry needs both weights and indices")
                raw_palette = geometry.findtext("BoneIDs") or ""
                try:
                    palette = [int(value) for value in raw_palette.replace(",", " ").split()] or list(range(len(nodes)))
                except ValueError as exc:
                    raise ValueError("Invalid geometry bone palette") from exc
                if len(palette) > 512 or any(value < 0 or value >= len(nodes) for value in palette):
                    raise ValueError("Geometry palette references a missing skeleton bone")
                for line in (vb.findtext("Data") or "").splitlines():
                    fields = line.split()
                    if not fields:
                        continue
                    if len(fields) != width:
                        raise ValueError("Skin vertex row does not match its layout")
                    try:
                        raw_weights = [int(value) for value in fields[offsets["BlendWeights"]:offsets["BlendWeights"] + 4]]
                        raw_indices = [int(value) for value in fields[offsets["BlendIndices"]:offsets["BlendIndices"] + 4]]
                    except ValueError as exc:
                        raise ValueError("Skin weights and palette indices must be decoded bytes") from exc
                    if any(not 0 <= value <= 255 for value in (*raw_weights, *raw_indices)) or not 253 <= sum(raw_weights) <= 257:
                        raise ValueError("Invalid skin byte weights; expected a near-unit total")
                    for weight, index in zip(raw_weights, raw_indices):
                        if weight and index >= len(palette):
                            raise ValueError("Weighted vertex references a missing palette entry")
                        weights.append(weight / 255)
                        indices.append(palette[index] if weight else 0)
            packet["vertex_count"] += len(parsed.vertices)
            packet["triangle_count"] += len(parsed.triangles)
            if packet["vertex_count"] > MAX_VERTICES or packet["triangle_count"] > MAX_TRIANGLES or len(packet["meshes"]) >= 128:
                # Retain exact owner/LOD choices so React can request a smaller
                # LOD. Never return a partially truncated pose as a valid mesh.
                packet.update(view_unavailable="Animation model exceeds 30,000 vertices / 40,000 triangles / 128 geometries; choose a lower LOD",
                              bones=[], meshes=[], vertex_count=0, triangle_count=0)
                return packet
            packet["meshes"].append({"skin": skin, "rigid_bone": rigid,
                "positions": _chunks([value for point in parsed.vertices for value in point]),
                "triangles": _chunks([value for face in parsed.triangles for value in face]),
                "weights": _chunks(weights), "indices": _chunks(indices)})
    if not packet["meshes"]:
        raise ValueError("Selected model LOD contains no geometry")
    return packet
