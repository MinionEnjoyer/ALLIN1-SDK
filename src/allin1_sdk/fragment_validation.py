"""Bounded CodeWalker fragment relationships, not an engine physics simulation.

Schema evidence: Frag.cs AssignChildrenSkeletonsAndBounds, FragPhysicsLOD XML,
FragPhysTypeChild XML, and ResourceBaseTypes.cs Matrix4F_s. Serialized matrix
rows are retained verbatim: no wheel corrections or assembly order is inferred
from CodeWalker's preview renderer.
"""
from copy import deepcopy
import math

from lxml import etree

from allin1_sdk import animation_model

SCOPE = ("Fragment child indices, group hierarchy, bone-tag references and authored "
         "matrix values; child geometry uses the fragment's primary skeleton, as "
         "assigned by the decoder. Matrices retain serialized row order, not an "
         "assembled world transform. Damage transitions, collision response, "
         "wheel corrections and in-game attachment behavior are not tested.")


def _integer(node, field, maximum):
    fields = node.findall(field)
    if len(fields) != 1:
        raise ValueError(f"Expected one {field}")
    return animation_model._integer(node, field, 0, maximum)


def _matrix(node, width):
    if len(node):
        raise ValueError("Matrix must contain raw numeric values, not XML children")
    tokens = (node.text or "").replace(",", " ").split()
    if len(tokens) != width * 4:
        raise ValueError(f"Expected {width * 4} serialized matrix values")
    values = [float(token) for token in tokens]
    if not all(math.isfinite(value) and abs(value) <= 1e12 for value in values):
        raise ValueError("Nonfinite or unbounded fragment matrix")
    rows = [values[i:i + width] for i in range(0, len(values), width)]
    a, b, c = [row[:3] for row in rows[:3]]
    det = a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0])
    return rows, abs(det) >= 1e-18


def inspect(root, owners, rig_owners, add, validate):
    if root.tag != "Fragment":
        return []
    lods = root.findall("Physics/LOD1") + root.findall("Physics/LOD2") + root.findall("Physics/LOD3")
    if not lods:
        return []
    if len(lods) > 3 or len({lod.tag for lod in lods}) != len(lods):
        add("attachments", "fail", "fragment_duplicate_lod", "Physics", "Duplicate physics LOD declarations")
        return []
    if sum(len(lod.findall("Children/Item")) for lod in lods) > 64:
        add("attachments", "not_checked", "fragment_child_limit", "Physics", "Fragment exceeds the 64-child validation bound; children were not checked.")
        return []
    owner = deepcopy(owners[0]) if len(owners) == 1 else None
    if owner is not None and 0 in rig_owners:
        try:
            animation_model.compatible_skeleton(owner, rig_owners[0])
            embedded = owner.find("Skeleton")
            if embedded is not None:
                owner.remove(embedded)
            owner.append(deepcopy(rig_owners[0].find("Skeleton")))
        except (ValueError, TypeError):
            owner = None
    bones = owner.findall("Skeleton/Bones/Item") if owner is not None else []
    tags = {}
    try:
        if len(bones) > 512:
            raise ValueError("Fragment skeleton exceeds 512 bones")
        for index, bone in enumerate(bones):
            tag = _integer(bone, "Tag", 65535)
            tags.setdefault(tag, []).append(index)
    except ValueError as exc:
        add("attachments", "fail", "fragment_skeleton_invalid", "Physics", str(exc))
        tags = {}
    add("attachments", "not_checked", "fragment_runtime_unverified", "Physics", SCOPE)
    records = []
    for lod in lods:
        location = f"Physics/{lod.tag}"
        if any(len(lod.findall(field)) > 1 for field in ("Children", "Groups", "Transforms")):
            add("attachments", "fail", "fragment_duplicate_array", location, "Duplicate child/group/transform arrays; no combined index mapping was inferred")
            continue
        children, groups, transforms = (lod.findall(path) for path in ("Children/Item", "Groups/Item", "Transforms/Item"))
        if len(groups) > 255 or len(transforms) > 512:
            add("attachments", "not_checked", "fragment_relationship_limit", location, "Group/transform arrays exceed bounded validation coverage")
            continue
        try:
            for group in groups:
                names = group.findall("Name")
                if len(names) != 1 or names[0].text is None:
                    raise ValueError("Expected one physics group name; absent names cannot be rebuilt by the decoder")
                if len(names[0].text) > 40 or any(ord(char) > 255 for char in names[0].text):
                    add("attachments", "warning", "fragment_group_name_loss", location, "Physics group name cannot round-trip unchanged through the decoder's 40-byte low-byte name field")
            parents = [_integer(group, "ParentIndex", 255) for group in groups]
            for index in range(len(parents)):
                seen, cursor = set(), index
                while cursor != 255:
                    if cursor >= len(parents):
                        raise ValueError("Physics group parent index is outside the group array")
                    if cursor in seen:
                        raise ValueError("Physics group parent cycle")
                    seen.add(cursor)
                    cursor = parents[cursor]
        except ValueError as exc:
            add("attachments", "fail", "fragment_group_hierarchy", location, str(exc))
        offset = None
        offsets = lod.findall("PositionOffset")
        if len(offsets) == 1:
            try:
                offset = [float(offsets[0].get(axis, "")) for axis in "xyz"]
                if not all(math.isfinite(v) and abs(v) <= 1e12 for v in offset):
                    raise ValueError("Invalid position offset")
            except ValueError:
                offset = None
                add("attachments", "fail", "fragment_position_offset", location, "Nonfinite or invalid authored position offset")
        else:
            add("attachments", "not_checked" if not offsets else "fail", "fragment_position_offset", location, "Expected one authored position offset; no default was inferred")
        matrices = []
        for index, node in enumerate(transforms):
            try:
                rows, invertible = _matrix(node, 4)
                matrices.append(rows)
                if not invertible:
                    add("attachments", "warning", "fragment_singular_transform", f"{location}/Transforms/{index}", "Singular basis cannot demonstrate an invertible placement; sentinel/unused transform semantics are unverified")
            except ValueError as exc:
                matrices.append(None)
                add("attachments", "fail", "fragment_transform_invalid", f"{location}/Transforms/{index}", str(exc))
        if len(transforms) != len(children):
            add("attachments", "not_checked", "fragment_transform_count", location, "Physics transform and child counts differ. Missing index mappings or extra transform roles remain unresolved, not silently padded.")
        for index, child in enumerate(children):
            child_path = f"{location}/Children/{index}"
            record = {"location": child_path, "child_index": index, "physics_lod": lod.tag,
                      "null_child": not any(isinstance(n.tag, str) for n in child),
                      "bone_tag": None, "bone_indices": [], "group_index": None,
                      "physics_matrix": matrices[index] if index < len(matrices) else None,
                      "position_offset": offset, "drawables": []}
            records.append(record)
            if record["null_child"]:
                continue
            try:
                tag = _integer(child, "BoneTag", 65535)
                record["bone_tag"] = tag
                matches = tags.get(tag, [])
                record["bone_indices"] = matches
                if len(matches) > 1:
                    add("attachments", "fail", "fragment_bone_ambiguous", child_path, "Physics child bone tag resolves to multiple primary skeleton bones")
                elif not matches:
                    add("attachments", "not_checked", "fragment_bone_unresolved", child_path, "Physics child bone tag has no unique primary/shared skeleton match; no fallback or sentinel interpretation was inferred")
                group = _integer(child, "GroupIndex", 65535)
                record["group_index"] = group
                if group >= len(groups):
                    add("attachments", "fail" if groups else "not_checked", "fragment_group_unresolved", child_path, "Child group index does not resolve in the supplied group array")
            except ValueError as exc:
                add("attachments", "fail", "fragment_child_reference", child_path, str(exc))
            for name in ("Drawable", "Drawable2"):
                variants = child.findall(name)
                if len(variants) > 1:
                    add("attachments", "fail", "fragment_duplicate_drawable", child_path, f"Duplicate {name} variant; no owner selected")
                    continue
                if not variants:
                    continue
                drawable = deepcopy(variants[0])
                variant_path = f"{child_path}/{name}"
                evidence = {"variant": name, "matrix": None, "static_status": "incomplete", "lod_metrics": []}
                record["drawables"].append(evidence)
                fields = drawable.findall("Matrix")
                try:
                    if len(fields) != 1:
                        raise LookupError("Expected one authored child drawable matrix; no identity fallback was inferred")
                    evidence["matrix"], invertible = _matrix(fields[0], 3)
                    if not invertible:
                        add("attachments", "warning", "fragment_singular_drawable", variant_path, "Serialized drawable basis is singular; unused/sentinel meaning is unverified")
                except LookupError as exc:
                    add("attachments", "not_checked", "fragment_drawable_matrix_missing", variant_path, str(exc))
                except ValueError as exc:
                    add("attachments", "fail", "fragment_drawable_matrix_invalid", variant_path, str(exc))
                if drawable.find("Matrices") is not None:
                    add("attachments", "not_checked", "fragment_additional_matrices", variant_path, "Additional drawable matrix IDs/capacity are outside this binding check")
                drawable.tag = "Drawable"
                # Decoder-assigned ownership, not name-based/user-rig guessing.
                try:
                    if owner is not None and owner.find("Skeleton") is not None:
                        animation_model.compatible_skeleton(drawable, owner)
                        embedded = drawable.find("Skeleton")
                        if embedded is not None:
                            drawable.remove(embedded)
                        drawable.append(deepcopy(owner.find("Skeleton")))
                    if drawable.find("ShaderGroup") is None and owner is not None and owner.find("ShaderGroup") is not None:
                        drawable.append(deepcopy(owner.find("ShaderGroup")))
                    result = validate(etree.tostring(drawable))
                    evidence["static_status"] = result["static_status"]
                    evidence["lod_metrics"] = result["lod_metrics"]
                    for check in result["checks"]:
                        if check["category"] in {"attachments", "metadata"}:
                            continue
                        for finding in check["findings"]:
                            add(check["category"], finding["status"], "fragment_" + finding["code"], variant_path + "/" + finding["location"], finding["message"])
                        if check["truncated"]:
                            add(check["category"], "not_checked", "fragment_findings_truncated", variant_path, "Additional child findings exceed the report bound")
                except ValueError as exc:
                    evidence["static_status"] = "fail"
                    add("skinning", "fail", "fragment_child_binding", variant_path, str(exc))
    return records
