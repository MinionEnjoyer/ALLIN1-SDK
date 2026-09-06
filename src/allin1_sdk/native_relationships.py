"""Read-only, exact-index relationship views over decoded CodeWalker XML."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
import re
import math

from lxml import etree
from allin1_sdk.native_assets import (
    _item_children, _direct_child, _child_text, _numeric_child,
    _position_attributes, _raw_vector_rows, _raw_integer_values, _path_records, _archetype_records,
)

SUPPORTED = {".ymap", ".ytyp", ".ynd", ".ynv", ".rel"}
MAX_XML = 16 * 1024**2
MAX_NODES = 1000
MAX_EDGES = 1800


def analyze(data: bytes, suffix: str):
    if suffix not in SUPPORTED:
        return None
    if not 0 < len(data) <= MAX_XML:
        raise ValueError("Relationship XML exceeds the 16 MiB analysis limit")
    if suffix == ".rel":
        from allin1_sdk import rel_relationships
        return rel_relationships.analyze(data)
    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, recover=False)
    root = etree.fromstring(data, parser)
    if root.getroottree().docinfo.doctype or any(isinstance(node, etree._Entity) for node in root.iter()):
        raise ValueError("Relationship XML cannot contain DTDs or entities")
    expected = {".ymap": "CMapData", ".ytyp": "CMapTypes", ".ynd": "NodeDictionary", ".ynv": "NavMesh"}
    if etree.QName(root).localname != expected[suffix]:
        raise ValueError(f"Unsupported {suffix} XML root: {etree.QName(root).localname}")
    nodes, edges, warnings = [], [], []
    ids = set()
    node_count = edge_count = 0

    def node(identity, label, kind, position=None, fields=None, search=None, vertices=None):
        nonlocal node_count
        for point in ([position] if position is not None else []) + (list(vertices) if vertices else []):
            if len(point) != 3 or any(not math.isfinite(value) or abs(value) > 1e9 for value in point):
                raise ValueError("Relationship coordinates are non-finite or outside the guarded world range")
        if identity in ids:
            return
        ids.add(identity); node_count += 1
        if len(nodes) < MAX_NODES:
            value = {"id": identity, "label": str(label)[:300], "kind": kind, "fields": fields or {}}
            if position is not None:
                value["position"] = list(position)
            if search:
                value["search"] = str(search)[:256]
            if vertices and len(vertices) <= 128:
                value["vertices"] = [list(point) for point in vertices]
            nodes.append(value)

    def edge(source, target, label, resolution="local"):
        nonlocal edge_count
        edge_count += 1
        if len(edges) < MAX_EDGES:
            edges.append({"source": source, "target": target, "label": label, "resolution": resolution})

    def reference(source, kind, value):
        if not value or value.startswith("(unspecified"):
            return
        identity = f"reference:{kind}:{value.casefold()}"
        node(identity, value, kind, search=value)
        edge(source, identity, kind, "external: unresolved")

    if suffix == ".ymap":
        items = list(_item_children(root, "entities"))
        for index, item in enumerate(items):
            position = _direct_child(item, "position")
            archetype = _child_text(item, "archetypeName") or "(unnamed archetype)"
            parent = int(_numeric_child(item, "parentIndex", context="YMAP", integer=True, default=-1))
            node(f"entity:{index}", f"{index}: {archetype}", "entity", _position_attributes(position, context="YMAP") if position is not None else None,
                 {"index": index, "archetype": archetype, "parent_index": parent, "lod_level": _child_text(item, "lodLevel"),
                  "lod_distance": _numeric_child(item, "lodDist", context="YMAP", default=0.0), "flags": _numeric_child(item, "flags", context="YMAP", integer=True, default=0)})
            if position is None:
                warnings.append(f"Entity {index} has no position; its original index is retained")
            reference(f"entity:{index}", "archetype", archetype)
            if parent >= 0:
                if parent >= len(items):
                    node(f"entity:{parent}", f"Missing entity {parent}", "missing parent")
                edge(f"entity:{index}", f"entity:{parent}", "parent", "local" if parent < len(items) else "missing parent index")
        if _child_text(root, "parent"):
            node("document", _child_text(root, "name") or "Map document", "map")
            reference("document", "parent map", _child_text(root, "parent"))
    elif suffix == ".ytyp":
        archetype_xml = _item_children(root, "archetypes")
        for index, item in enumerate(_archetype_records(root)):
            identity = f"archetype:{index}"
            node(identity, item.name, "archetype", fields=asdict(item), search=item.asset_name)
            for kind, value in [("asset", item.asset_name), ("texture dictionary", item.texture_dictionary),
                                ("drawable dictionary", item.drawable_dictionary), ("physics dictionary", item.physics_dictionary),
                                ("clip dictionary", item.clip_dictionary)]:
                reference(identity, kind, value)
            if item.kind != "CMloArchetypeDef":
                continue
            definition = archetype_xml[index]
            entities = _item_children(definition, "entities")
            rooms = _item_children(definition, "rooms")

            def attachment(owner, target_index, category, count, label):
                target = f"{identity}:{category}:{target_index}"
                valid = 0 <= target_index < count
                if not valid:
                    node(target, f"Missing {category} {target_index}", f"missing MLO {category}")
                edge(owner, target, label, "local" if valid else f"missing {category} index")

            def attached_objects(owner, element):
                for target in _raw_integer_values(_direct_child(element, "attachedObjects"), context="MLO attached objects"):
                    attachment(owner, target, "entity", len(entities), "attached object")

            for entity_index, entity in enumerate(entities):
                key = f"{identity}:entity:{entity_index}"
                position = _direct_child(entity, "position")
                name = _child_text(entity, "archetypeName") or "(unnamed archetype)"
                parent = int(_numeric_child(entity, "parentIndex", context="MLO entity", integer=True, default=-1))
                node(key, f"{item.name} · entity {entity_index}: {name}", "MLO entity",
                     _position_attributes(position, context="MLO entity") if position is not None else None,
                     {"archetype_index": index, "entity_index": entity_index, "parent_index": parent, "coordinate_space": "archetype local"})
                edge(identity, key, "contains entity")
                reference(key, "archetype", name)
                if parent >= 0:
                    attachment(key, parent, "entity", len(entities), "parent")
            for room_index, room in enumerate(rooms):
                key = f"{identity}:room:{room_index}"
                low, high = _direct_child(room, "bbMin"), _direct_child(room, "bbMax")
                bounds = [_position_attributes(value, context="MLO room bounds") for value in (low, high)] if low is not None and high is not None else []
                center = [(bounds[0][axis] + bounds[1][axis]) / 2 for axis in range(3)] if bounds else None
                node(key, f"{item.name} · room {room_index}: {_child_text(room, 'name')}", "MLO room", center,
                     {"archetype_index": index, "room_index": room_index, "bounds": bounds, "coordinate_space": "archetype local",
                      "flags": _numeric_child(room, "flags", context="MLO room", integer=True, default=0)})
                edge(identity, key, "contains room")
                attached_objects(key, room)
                reference(key, "timecycle", _child_text(room, "timecycleName"))
            for portal_index, portal in enumerate(_item_children(definition, "portals")):
                key = f"{identity}:portal:{portal_index}"
                # MetaXml.WriteItemArray(FormatVector4) emits comma-separated Item text.
                corners = []
                for corner in _item_children(portal, "corners"):
                    values = [float(value.strip()) for value in (corner.text or "").split(",")]
                    if len(values) != 4 or not all(math.isfinite(value) for value in values):
                        raise ValueError("MLO portal corner must contain four finite coordinates")
                    corners.append(values[:3])
                center = [sum(point[axis] for point in corners) / len(corners) for axis in range(3)] if corners else None
                node(key, f"{item.name} · portal {portal_index}", "MLO portal", center,
                     {"archetype_index": index, "portal_index": portal_index, "coordinate_space": "archetype local",
                      "flags": _numeric_child(portal, "flags", context="MLO portal", integer=True, default=0)}, vertices=corners)
                edge(identity, key, "contains portal")
                for field in ("roomFrom", "roomTo"):
                    target = int(_numeric_child(portal, field, context="MLO portal", integer=True, default=-1))
                    attachment(key, target, "room", len(rooms), field)
                attached_objects(key, portal)
            sets = _item_children(definition, "entitySets")
            for set_index, entity_set in enumerate(sets):
                key = f"{identity}:entity-set:{set_index}"
                node(key, _child_text(entity_set, "name") or f"Entity set {set_index}", "MLO entity set", fields={
                    "archetype_index": index, "entity_set_index": set_index, "entity_count": len(_item_children(entity_set, "entities")),
                    "raw_locations": list(_raw_integer_values(_direct_child(entity_set, "locations"), context="MLO entity set"))})
                edge(identity, key, "contains entity set")
            if sets:
                warnings.append(f"{item.name}: entity-set locations are raw values; activation/room assignment is not resolved")
    elif suffix == ".ynd":
        records, junctions, vehicles, peds, junction_refs = _path_records(root)
        lookup = defaultdict(list)
        for index, item in enumerate(records):
            lookup[(item.area_id, item.node_id)].append(index)
            fields = asdict(item); fields.pop("links"); fields.pop("position")
            node(f"node:{index}", f"{item.area_id}:{item.node_id} · {item.street}", "vehicle node" if item.vehicle else "ped node", item.position, fields)
        for index, item in enumerate(records):
            for link in item.links:
                matches = lookup[(link.to_area, link.to_node)]
                target = f"node:{matches[0]}" if len(matches) == 1 else f"external-node:{link.to_area}:{link.to_node}"
                if len(matches) != 1:
                    node(target, f"{link.to_area}:{link.to_node}", "unresolved path node", fields={"area_id": link.to_area, "node_id": link.to_node})
                edge(f"node:{index}", target, f"path link · length {link.length} · flags {list(link.flags)}", "local" if len(matches) == 1 else "ambiguous duplicate ID" if matches else "external: unresolved")
        for index, junction in enumerate(junctions):
            node(f"junction:{index}", f"Junction {index}", "junction", junction.position, asdict(junction))
        if vehicles + peds != len(records):
            warnings.append("Declared vehicle/ped counts differ from the node inventory")
        if junction_refs:
            warnings.append(f"{junction_refs} junction references are counted but not resolved by this view")
    else:
        area = int(_numeric_child(root, "AreaID", context="YNV", integer=True, default=-1))
        polygons = list(_item_children(root, "Polygons"))
        for index, item in enumerate(polygons):
            vertices = _raw_vector_rows(_direct_child(item, "Vertices"), context="YNV")
            center = tuple(sum(point[axis] for point in vertices) / len(vertices) for axis in range(3)) if vertices else None
            node(f"poly:{index}", f"{area}:{index}", "polygon", center, {"area_id": area, "polygon_index": index, "vertex_count": len(vertices), "flags": _child_text(item, "Flags")}, vertices=vertices)
            if len(vertices) > 128:
                warnings.append(f"Polygon {index} exceeds 128 display vertices; centroid only")
            for row in _child_text(item, "Edges").splitlines():
                if not row.strip():
                    continue
                match = re.fullmatch(r"\s*(\d+):(\d+)\s*,\s*(\d+):(\d+)\s*", row)
                if not match:
                    warnings.append(f"Polygon {index} has an unsupported edge row")
                    continue
                a, p, b, q = map(int, match.groups())
                for target_area, target_poly in {(a, p), (b, q)}:
                    if (target_area, target_poly) == (area, index) or target_poly == 16383 or target_area == 16383:
                        continue
                    local = target_area == area and target_poly < len(polygons)
                    target = f"poly:{target_poly}" if local else f"external-poly:{target_area}:{target_poly}"
                    if not local:
                        node(target, f"{target_area}:{target_poly}", "unresolved polygon")
                    edge(f"poly:{index}", target, "adjacent polygon", "local" if local else "external or missing polygon")
        for index, item in enumerate(_item_children(root, "Portals")):
            start = _position_attributes(_direct_child(item, "PositionFrom"), context="YNV portal")
            end = _position_attributes(_direct_child(item, "PositionTo"), context="YNV portal")
            node(f"portal:{index}", f"Portal {index}", "portal", start, {"position_to": list(end)}, vertices=[start, end])
            for field in ("PolyFrom", "PolyTo"):
                target = int(_numeric_child(item, field, context="YNV portal", integer=True, default=-1))
                if target >= 0:
                    if target >= len(polygons):
                        node(f"poly:{target}", f"Missing polygon {target}", "missing polygon")
                    edge(f"portal:{index}", f"poly:{target}", field, "local" if target < len(polygons) else "missing polygon")
        for index, item in enumerate(_item_children(root, "Points")):
            node(f"point:{index}", f"Point {index}", "navigation point", _position_attributes(_direct_child(item, "Position"), context="YNV point"),
                 {"type": _numeric_child(item, "Type", context="YNV point", integer=True, default=0)})
    truncated = node_count > len(nodes) or edge_count > len(edges)
    if truncated:
        warnings.append(f"Display limited to {MAX_NODES} nodes / {MAX_EDGES} relationships; totals include omitted rows")
    return {"schema_version": 1, "format": suffix, "nodes": nodes, "edges": edges,
            "node_count": node_count, "edge_count": edge_count, "truncated": truncated,
            "warnings": warnings[:100], "read_only": True,
            "scope": "Decoded document only. External names/hashes are references, not proof of installed asset resolution."
                     + (" MLO coordinates are archetype-local, not world placement; different interiors may overlap." if suffix == ".ytyp" else "")}
