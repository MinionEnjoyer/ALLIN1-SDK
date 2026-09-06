"""Explicit weapon attachment bindings and authored anchor frames, not game assembly.

No filename fallback, load-order winner, child-origin convention or game pose is
inferred. Frames are diagnostic skeleton bind frames in column-vector notation.
"""
from collections import defaultdict
from copy import deepcopy
import math

from lxml import etree

from allin1_sdk import animation_model, metadata_validation
from allin1_sdk.native_assets import _model_trs_matrix, _multiply_model_matrices


def _identity(node, field):
    text = (node.findtext(field) or "").strip()
    if not text or len(text) > 160:
        raise ValueError(f"Missing or unbounded attachment {field}")
    return text


def definitions(data, source):
    if len(data) > metadata_validation.MAX_BYTES:
        raise ValueError("Attachment metadata exceeds 8 MiB")
    root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True))
    if root.getroottree().docinfo.doctype:
        raise ValueError("Attachment metadata DTDs are not supported")
    components, links = [], []
    if root.tag == "CWeaponComponentInfoBlob":
        for item in root.findall("Infos/Item"):
            # Non-rendered components are not promoted to model attachments.
            model = item.find("Model")
            if model is None or model.get("null", "").casefold() == "true":
                continue
            components.append({"name": _identity(item, "Name"), "model": _identity(item, "Model"),
                               "source": source, "declared_bone": (item.findtext("AttachBone") or "").strip()})
    elif root.tag == "CWeaponInfoBlob":
        for item in root.findall(".//Infos/Item"):
            if item.find("Name") is None or (item.find("AmmoInfo") is None and item.get("type") != "CWeaponInfo"):
                continue
            for point in item.findall("AttachPoints/Item"):
                for component in point.findall("Components/Item"):
                    links.append({"weapon": _identity(item, "Name"), "model": _identity(item, "Model"),
                                  "bone": _identity(point, "AttachBone"), "component": _identity(component, "Name"), "source": source})
    if len(components) + len(links) > 2000:
        raise ValueError("Attachment metadata exceeds 2,000 records")
    return components, links


def anchor(owner, name):
    """Strictly validate the entire owning hierarchy before returning an anchor."""
    nodes = owner.findall("Skeleton/Bones/Item")
    if not nodes:
        raise LookupError("No embedded skeleton; an explicit shared rig is required")
    if len(nodes) > 512:
        raise LookupError("Skeleton exceeds the 512-bone bound")
    parents, matrices, tags = [], [], []
    for index, node in enumerate(nodes):
        if animation_model._integer(node, "Index", 0, 511) != index:
            raise ValueError("Bone indices disagree with array order")
        parents.append(animation_model._integer(node, "ParentIndex", -1, len(nodes)-1))
        tags.append(animation_model._integer(node, "Tag", 0, 65535))
        for field, axes in (("Translation", "xyz"), ("Rotation", "xyzw"), ("Scale", "xyz")):
            element = node.find(field)
            if element is None:
                raise ValueError(f"Missing bone {field}")
            values = [float(element.get(axis, "")) for axis in axes]
            if not all(math.isfinite(v) and abs(v) <= 1e6 for v in values):
                raise ValueError(f"Invalid bone {field}")
            if field == "Scale" and any(abs(v) < 1e-6 for v in values):
                raise ValueError("Singular bone scale")
            if field == "Rotation" and not math.isclose(math.hypot(*values), 1, abs_tol=1e-4):
                raise ValueError("Anchor rotation is not a unit quaternion; no silent normalization")
        matrices.append(_model_trs_matrix(node))
    if len(tags) != len(set(tags)):
        raise ValueError("Duplicate bone tags")
    frames = {}
    for index in range(len(nodes)):
        chain, cursor = [], index
        while cursor != -1 and cursor not in frames:
            if cursor in chain:
                raise ValueError("Bone-parent cycle")
            chain.append(cursor)
            cursor = parents[cursor]
        for child in reversed(chain):
            parent = parents[child]
            frames[child] = matrices[child] if parent == -1 else _multiply_model_matrices(frames[parent], matrices[child])
            if not all(math.isfinite(v) and abs(v) <= 1e12 for row in frames[child] for v in row):
                raise ValueError("Unbounded composed anchor transform")
            a, b, c = (row[:3] for row in frames[child][:3])
            determinant = a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0])
            if not math.isfinite(determinant) or abs(determinant) < 1e-18:
                raise ValueError("Singular or numerically unstable composed anchor transform")
    matches = [i for i, node in enumerate(nodes) if (node.findtext("Name") or "").casefold() == name.casefold()]
    if len(matches) != 1:
        raise ValueError(f"Expected one named anchor {name}; found {len(matches)}")
    index = matches[0]
    return {"bone_index": index, "bone_tag": tags[index], "local_matrix": [list(row) for row in matrices[index]], "skeleton_matrix": [list(row) for row in frames[index]]}


class Attachments:
    def __init__(self, add):
        self.add = add
        self.components = defaultdict(list)
        self.models = defaultdict(list)
        self.links = []

    def metadata(self, data, source, *, include_links=True):
        components, links = definitions(data, source)
        if sum(map(len, self.components.values())) + len(self.links) + len(components) + (len(links) if include_links else 0) > 8000:
            raise ValueError("Attachment context exceeds 8,000 records")
        for component in components:
            self.components[component["name"].casefold()].append(component)
        if include_links:
            self.links.extend(links)

    def model(self, key, source, owners, *, rig_owners=None):
        if rig_owners:
            owners=[deepcopy(owner) for owner in owners]
            for index,rig in rig_owners.items():
                try:
                    animation_model.compatible_skeleton(owners[index],rig)
                except ValueError as exc:
                    self.add("attachments","fail","attachment_rig_conflict",source,str(exc))
                    continue
                existing=owners[index].find("Skeleton")
                if existing is not None: owners[index].remove(existing)
                owners[index].append(deepcopy(rig.find("Skeleton")))
        self.models[key.casefold()].append((source, owners))

    def resolve(self):
        evidence = []
        def finding(status, code, location, message):
            self.add("attachments", status, code, location, message)
        if not self.links:
            finding("not_checked", "attachment_context_missing", "package", "No supported weapon attachment links were supplied. Other attachment schemas and runtime assemblies remain unverified.")
        for link in self.links:
            location = f"{link['source']}/{link['weapon']}/{link['component']}"
            components = self.components[link["component"].casefold()]
            if len(components) != 1:
                finding("not_checked", "attachment_component_unresolved", location, f"Expected one component definition; found {len(components)}. No load-order winner inferred.")
                continue
            component = components[0]
            parent = self.models[link["model"].casefold()]
            child = self.models[component["model"].casefold()]
            if len(parent) != 1 or len(child) != 1:
                finding("not_checked", "attachment_model_unresolved", location, f"Expected one parent and child model; found {len(parent)} and {len(child)}. Supply exact assets; no filename or load-order fallback.")
                continue
            if len(parent[0][1]) != 1 or len(child[0][1]) != 1:
                finding("not_checked", "attachment_owner_ambiguous", location, "Multiple drawable owners require an explicit assembly owner selection.")
                continue
            try:
                frame = anchor(parent[0][1][0], link["bone"])
            except LookupError as exc:
                finding("not_checked", "attachment_shared_rig_required", location, str(exc))
                continue
            except (ValueError, TypeError) as exc:
                finding("fail", "attachment_anchor_invalid", location, str(exc))
                continue
            if component["declared_bone"] and component["declared_bone"].casefold() != link["bone"].casefold():
                finding("warning", "attachment_bone_declarations_differ", location, "Weapon and component AttachBone declarations differ; precedence and runtime placement are not inferred.")
            if len(evidence) >= 1000:
                raise ValueError("Attachment evidence exceeds 1,000 resolved links")
            evidence.append({**link, "parent_source": parent[0][0], "child_source": child[0][0],
                             "component_source": component["source"], "component_declared_bone": component["declared_bone"], **frame})
            finding("pass", "attachment_anchor_resolved", location, f"Exact parent/child asset and one parent anchor '{link['bone']}' resolved; authored hierarchy transforms are finite and nonsingular.")
        if self.links:
            finding("not_checked", "attachment_runtime_unverified", "package", "Authored anchor frames are static evidence only: child-origin/offset conventions, animated placement, clipping and in-game assembly have not been proved.")
        return evidence
