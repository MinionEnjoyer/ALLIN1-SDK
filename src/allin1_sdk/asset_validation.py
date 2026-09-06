"""Evidence-scoped model validation, independent of authoring or installation.

No all-clear is inferred from successful decoding. Missing package/attachment
context stays explicit, and report identity binds the exact analyzed XML.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from lxml import etree

from allin1_sdk import __version__, animation_model, fragment_validation
from allin1_sdk.native_assets import _read_model_geometry
from allin1_sdk.implementation_identity import identify

RULESET = "asset-validation/1"
CATEGORIES = ("skeleton", "attachments", "skinning", "textures", "lods", "metadata")


def analyze(data: bytes, *, source_identity=None, rig_owners=None):
    owners = animation_model._drawables(data)
    rig_owners = rig_owners or {}
    if not isinstance(rig_owners,dict) or any(type(index) is not int or not 0<=index<len(owners) for index in rig_owners):
        raise ValueError("Shared rig binding must select an exact model drawable")
    rig_evidence=[]
    checks = {key: {"category": key, "status": "pass", "findings": [], "finding_count": 0} for key in CATEGORIES}
    rank = {"pass": 0, "warning": 1, "not_checked": 2, "fail": 3}

    def finding(category, status, code, location, message):
        check = checks[category]
        if rank[status] > rank[check["status"]]:
            check["status"] = status
        check["finding_count"] += 1
        if len(check["findings"]) < 40:
            check["findings"].append({"code": code, "status": status, "location": location[:256], "message": message[:500]})

    finding("attachments", "not_checked", "attachment_context_missing", "package",
            "Attachment definitions, intended parent rigs and assembled transforms are not supplied. Bone validity alone cannot prove attachment placement or animation behavior.")
    finding("metadata", "not_checked", "metadata_context_missing", "package",
            "Package metadata and the installed/load-order namespace are not supplied; metadata name/hash collisions have not been checked.")
    if len(owners) > 1:
        finding("skeleton", "warning", "multiple_drawable_owners", "model",
                f"{len(owners)} drawable owners. This report checks each independently; animation and attachment consumers must select the intended skeleton explicitly.")
    metrics = [];distances=[]
    for ordinal, owner in enumerate(owners):
        location = f"drawable:{ordinal}"
        isolated = deepcopy(owner)
        isolated.tag = "Drawable"
        owner_data = etree.tostring(isolated)
        nodes = owner.findall("Skeleton/Bones/Item")
        skeleton_data=None
        if ordinal in rig_owners:
            shared=deepcopy(rig_owners[ordinal]);shared.tag="Drawable"
            skeleton_data=etree.tostring(shared)
            rig_evidence.append({"drawable":ordinal,"selected_skeleton_xml_sha256":hashlib.sha256(skeleton_data).hexdigest()})
            try:
                nodes=animation_model.compatible_skeleton(owner,shared)
                finding("skeleton","warning","shared_skeleton_explicit",location,"Using the explicitly selected shared rig. Bind compatibility is checked, but the game-intended rig and runtime behavior require independent evidence.")
            except ValueError as exc:
                finding("skeleton","fail","shared_skeleton_conflict",location,str(exc))
        if len(nodes) > 512:
            finding("skeleton", "not_checked", "bone_limit", location, "Skeleton exceeds the 512-bone validation bound.")
            for category in ("skinning", "textures", "lods"):
                finding(category, "not_checked", "owner_limit", location, "This drawable was not fully checked because its skeleton exceeds the validation bound.")
            continue
        if not nodes:
            finding("skeleton", "not_checked", "shared_skeleton_required", location,
                    "No embedded skeleton. A static drawable may not need one, but shared-rig binding and ambiguity cannot be validated without its rig.")
        tags, names, parents = [], [], []
        try:
            for index, node in enumerate(nodes):
                if animation_model._integer(node, "Index", 0, 511) != index:
                    raise ValueError("Bone indices disagree with XML array order")
                tags.append(animation_model._integer(node, "Tag", 0, 65535))
                parents.append(animation_model._integer(node, "ParentIndex", -1, len(nodes)-1))
                names.append((node.findtext("Name") or "").casefold())
                for field, axes in (("Translation", "xyz"), ("Rotation", "xyzw"), ("Scale", "xyz")):
                    value = node.find(field)
                    if value is None:
                        raise ValueError(f"Missing bone {field}")
                    components = [float(value.get(axis, "")) for axis in axes]
                    if not all(math.isfinite(v) and abs(v) <= 1e6 for v in components):
                        raise ValueError(f"Nonfinite or unbounded bone {field}")
                    if field == "Scale" and any(abs(v) < 1e-6 for v in components):
                        raise ValueError("Singular bone scale")
                    if field == "Rotation":
                        norm = math.hypot(*components)
                        if norm < 1e-9:
                            raise ValueError("Zero bone quaternion")
                        if not math.isclose(norm, 1, abs_tol=1e-4):
                            finding("skeleton", "warning", "quaternion_normalization", f"{location}/bone:{index}", "Bone rotation requires normalization; source values are not a unit quaternion.")
            if len(tags) != len(set(tags)):
                raise ValueError("Duplicate bone tags make animation binding ambiguous")
            for index in range(len(parents)):
                visited, cursor = set(), index
                while cursor != -1:
                    if cursor in visited:
                        raise ValueError("Bone-parent cycle")
                    visited.add(cursor)
                    cursor = parents[cursor]
            duplicates = [name for name, count in Counter(names).items() if name and count > 1]
            if duplicates:
                finding("skeleton", "warning", "duplicate_bone_names", location, "Ambiguous name-based binding: " + ", ".join(duplicates)[:350])
        except (ValueError, TypeError) as exc:
            finding("skeleton", "fail", "invalid_skeleton", location, str(exc))

        available = [lod for lod in animation_model.LODS if owner.findall(f"DrawableModels{lod}/Item")]
        previous_distance=None
        # Exact CodeWalker Drawable XML field names; values remain authored
        # distances, not a prediction of engine-scaled transition locations.
        for lod,field in zip(animation_model.LODS,("LodDistHigh","LodDistMed","LodDistLow","LodDistVlow")):
            fields=owner.findall(field);distance=None;distance_status="not_supplied"
            if fields:
                try:
                    if len(fields)!=1: raise ValueError("Duplicate authored LOD distance")
                    distance=float(fields[0].get("value",""))
                    if not math.isfinite(distance) or abs(distance)>3.4028234663852886e38:
                        raise ValueError("LOD distance must be a finite float32 value")
                    distance_status="valid_authored_value"
                    if distance<0:
                        distance_status="negative_semantics_unverified"
                        finding("lods","warning","negative_lod_distance",f"{location}/{lod}","Authored LOD distance is negative. This is not a positive transition range; any engine-specific sentinel/fallback meaning remains unverified, not assumed invalid.")
                    if lod in available:
                        if distance==0:
                            finding("lods","warning","zero_lod_distance",f"{location}/{lod}","Populated LOD has an authored distance of zero; no positive authored range is demonstrated. Engine activation still requires runtime evidence.")
                        if distance>=0 and previous_distance is not None and distance<=previous_distance:
                            finding("lods","warning","nonincreasing_lod_distance",f"{location}/{lod}","Populated lower LOD does not have a larger authored distance than the preceding populated LOD. Review transition settings; no engine-side fallback or multiplier is assumed.")
                        previous_distance=distance if distance>=0 else None
                except (ValueError,TypeError) as exc:
                    distance=None;distance_status="invalid"
                    finding("lods","fail","invalid_lod_distance",f"{location}/{lod}",str(exc))
            elif lod in available:
                finding("lods","not_checked","lod_distance_missing",f"{location}/{lod}","Populated LOD has no authored distance in this XML; transition settings remain unknown.")
            distances.append({"drawable":ordinal,"lod":lod,"field":field,"distance":distance,"status":distance_status,"models_present":lod in available})
        previous = None
        if not available:
            finding("lods", "not_checked", "no_model_lods", location, "No primary model LOD geometry was supplied.")
            finding("skinning", "not_checked", "no_model_lods", location, "No primary model LOD geometry was supplied.")
        elif len(available) == 1:
            finding("lods", "warning", "single_lod", location, "Only one LOD is present. No lower-detail cost reduction can be measured.")
        if available:
            finding("lods", "not_checked", "lod_activation_unverified", location, "Authored distances and geometry reductions are checked separately. Engine-side multipliers, actual transitions, visual quality and game residency still require runtime evidence.")
        for lod in available:
            models = owner.findall(f"DrawableModels{lod}/Item")
            geometries = [g for model in models for g in model.findall("Geometries/Item")]
            if len(geometries) > 128:
                finding("lods", "not_checked", "geometry_limit", f"{location}/{lod}", "LOD exceeds the 128-geometry validation bound.")
                finding("skinning", "not_checked", "geometry_limit", f"{location}/{lod}", "Skinning checks exceed the geometry bound.")
                continue
            vertices = triangles = 0
            complete = True
            for geometry in geometries:
                vb = geometry.find("VertexBuffer")
                try:
                    parsed = _read_model_geometry(vb) if vb is not None else None
                except ValueError as exc:
                    finding("lods", "not_checked" if "limit" in str(exc).casefold() else "fail", "geometry_decode", f"{location}/{lod}", str(exc))
                    complete = False
                    continue
                if parsed is None or not parsed.triangles:
                    complete = False
                    continue
                vertices += len(parsed.vertices)
                triangles += len(parsed.triangles)
            if not complete or not geometries:
                finding("lods", "not_checked", "geometry_unavailable", f"{location}/{lod}", "Geometry could not be fully measured; partial counts are not treated as effective LODs.")
            metrics.append({"drawable": ordinal, "lod": lod, "vertices": vertices, "triangles": triangles, "complete": complete and bool(geometries)})
            if complete and geometries:
                if previous and (triangles >= previous[1] or vertices >= previous[0]):
                    finding("lods", "warning", "ineffective_lod", f"{location}/{lod}", "This lower LOD does not reduce both measured vertices and triangles relative to the preceding populated LOD.")
                previous = (vertices, triangles)
            else:
                previous = None
            try:
                bound = animation_model.analyze(owner_data, "0", lod, skeleton_data=skeleton_data, skeleton_drawable="0" if skeleton_data else None)
                unavailable = bound.get("binding_required") or bound.get("view_unavailable")
                if unavailable:
                    finding("skinning", "not_checked", "binding_unavailable", f"{location}/{lod}", unavailable)
            except ValueError as exc:
                message = str(exc)
                unsupported = any(term in message.casefold() for term in ("unsupported", "currently requires", "no supported", "exceeds"))
                finding("skinning", "not_checked" if unsupported else "fail", "skin_binding", f"{location}/{lod}", message)
        # Only embedded texture names are available here. Unresolved samplers
        # may be legitimate shared-game dependencies, not proven missing files.
        embedded = [(item.findtext("Name") or "").casefold() for item in owner.findall("ShaderGroup/TextureDictionary/Item")]
        for name, count in Counter(embedded).items():
            if name and count > 1:
                finding("textures", "fail", "duplicate_texture_name", location, f"Embedded texture name is duplicated: {name[:200]}")
        refs = { (item.findtext("Name") or "").casefold() for item in owner.findall("ShaderGroup/Shaders/Item/Parameters/Item") if item.get("type", "").casefold() == "texture" }
        if not owner.findall("ShaderGroup/Shaders/Item"):
            finding("textures", "not_checked", "shader_context_missing", location, "No shader definitions are supplied; texture dependency coverage is unknown.")
        if "" in refs:
            finding("textures", "not_checked", "unbound_texture_slot", location, "Shader has unbound texture slots. Whether each slot is optional requires shader/material-role context; an empty slot alone is not a proven missing dependency.")
        unresolved = sorted(refs - set(embedded) - {""})
        for name in unresolved:
            finding("textures", "not_checked", "external_texture_unresolved", location, f"{name[:200]} is not embedded. Supply the matching texture dictionaries/shared-game context before calling it missing or resolved.")
        if refs & set(embedded) - {""}:
            finding("textures", "not_checked", "embedded_payload_unverified", location, "Embedded names resolve, but this model-XML check does not validate texture payload bytes, formats or mip contents.")
    fragments = fragment_validation.inspect(owners[0].getroottree().getroot(), owners, rig_owners, finding, analyze)
    for check in checks.values():
        check["truncated"] = check["finding_count"] > len(check["findings"])
    implementation=identify((__file__,animation_model.__file__,fragment_validation.__file__,str(Path(__file__).with_name("native_assets.py"))))
    report = {"schema_version": 1, "ruleset": RULESET, "sdk_version": __version__, "read_only": True,
              "source_identity": source_identity or {},
              "validator_sha256":implementation["sha256"],"validator_identity":implementation,
              "source_sha256": hashlib.sha256(data).hexdigest(), "source_kind": "exported_model_xml",
              "static_status": "fail" if any(c["status"] == "fail" for c in checks.values()) else "incomplete" if any(c["status"] == "not_checked" for c in checks.values()) else "warning" if any(c["status"] == "warning" for c in checks.values()) else "pass",
              "runtime_status": "not_tested", "checks": list(checks.values()), "lod_metrics": metrics,"lod_distances":distances,"shared_rigs":rig_evidence,
              "fragment_children":fragments,"fragment_scope":fragment_validation.SCOPE,
              "scope": "Primary drawable and bounded fragment-child XML checks. Authored physics relationships are separate from attachment assembly, physics simulation, animation execution, installed metadata namespace, measured GPU residency or in-game proof. LOD counts are geometry costs, not measured memory or visual quality."}
    report["report_sha256"] = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return report
