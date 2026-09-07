"""Explicit pairwise bind-frame placement; never infer an engine attachment rule."""
from copy import deepcopy
import math

from lxml import etree

from allin1_sdk import animation_model, attachment_validation
from allin1_sdk.artifact_contract import digest
from allin1_sdk.native_assets import _multiply_model_matrices as multiply

SCOPE = ("Independent user-declared parent/child bind-frame pairs. Child-to-parent = "
         "parent anchor × declared local rigid offset × inverse(child anchor). The "
         "offset is translation × rotation (column vectors); its translation uses "
         "parent-anchor axes. Optional rotation is an XYZW unit quaternion, with "
         "identity used only when omitted. No silent normalization. Empty "
         "bone names explicitly select model origin. This verifies affine placement "
         "and anchor agreement, not engine attachment conventions, animated poses, "
         "clipping, chained assemblies or fragment physics placement.")
IDENTITY = [[int(row == column) for column in range(4)] for row in range(4)]


def local_offset(binding):
    offset = binding["offset"]
    if not isinstance(offset, list) or len(offset) != 3 or not all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 1e6 for v in offset):
        raise ValueError("Assembly translation requires three finite bounded numbers")
    rotation = binding.get("rotation", [0, 0, 0, 1])
    if (not isinstance(rotation, list) or len(rotation) != 4
            or not all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 1 for v in rotation)
            or not math.isclose(math.hypot(*rotation), 1, rel_tol=0, abs_tol=1e-6)):
        raise ValueError("Assembly rotation requires a unit XYZW quaternion; no silent normalization")
    x, y, z, w = rotation
    return [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w), offset[0]],
            [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w), offset[1]],
            [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y), offset[2]],
            [0, 0, 0, 1]]


def determinant(matrix):
    a, b, c = matrix[0][:3]; d, e, f = matrix[1][:3]; g, h, i = matrix[2][:3]
    return a*(e*i-f*h)-b*(d*i-f*g)+c*(d*h-e*g)


def inverse(matrix):
    if not all(math.isfinite(v) and abs(v) <= 1e12 for row in matrix for v in row):
        raise ValueError("Unbounded assembly transform")
    a, b, c = matrix[0][:3]; d, e, f = matrix[1][:3]; g, h, i = matrix[2][:3]
    det = determinant(matrix)
    if not math.isfinite(det) or abs(det) < 1e-18:
        raise ValueError("Singular assembly anchor")
    rows = [[(e*i-f*h)/det, (c*h-b*i)/det, (b*f-c*e)/det],
            [(f*g-d*i)/det, (a*i-c*g)/det, (c*d-a*f)/det],
            [(d*h-e*g)/det, (b*g-a*h)/det, (a*e-b*d)/det]]
    condition = max(sum(abs(v) for v in row[:3]) for row in matrix[:3]) * max(sum(abs(v) for v in row) for row in rows)
    if not math.isfinite(condition) or condition > 1e10:
        raise ValueError("Numerically unstable assembly anchor")
    return [row + [-sum(row[j]*matrix[j][3] for j in range(3))] for row in rows] + [[0, 0, 0, 1]]


def inspect(bindings, documents, selected_rigs, add):
    if bindings is None:
        bindings = []
    if not isinstance(bindings, list) or len(bindings) > 32:
        raise ValueError("Choose at most 32 declared assembly pairs")
    evidence, seen = [], set()
    fields = {"parent", "parent_drawable", "parent_sha256", "parent_bone", "child", "child_drawable", "child_sha256", "child_bone", "offset"}
    for binding in bindings:
        if not isinstance(binding, dict) or not fields <= set(binding) or set(binding) - fields - {"rotation"}:
            raise ValueError("Assembly requires exact owners, source hashes, anchor names and local translation")
        owners = {}
        for role in ("parent", "child"):
            key, index = binding[role], binding[role + "_drawable"]
            if not isinstance(key, str) or key not in documents or type(index) is not int or not 0 <= index < len(documents[key][2]):
                raise ValueError("Assembly owner is unavailable; inspect the selected context again")
            if binding[role + "_sha256"] != documents[key][3]:
                raise ValueError("Assembly source bytes changed; select current owners again")
            bone = binding[role + "_bone"]
            if not isinstance(bone, str) or len(bone) > 160 or bone != bone.strip() or any(ord(c) < 32 for c in bone):
                raise ValueError("Assembly bone names must be bounded exact names")
            owners[role] = deepcopy(documents[key][2][index])
        if (binding["parent"], binding["parent_drawable"]) == (binding["child"], binding["child_drawable"]):
            raise ValueError("Choose distinct parent and child drawable owners")
        local = local_offset(binding)
        identity = digest(binding)
        if identity in seen:
            raise ValueError("Duplicate declared assembly pair")
        seen.add(identity)
        record = {**binding, "binding_sha256": identity, "status": "not_checked", "message": "",
                  "parent_anchor_matrix": None, "child_anchor_matrix": None,
                  "child_to_parent_matrix": None, "relative_anchor_error": None,
                  "orientation_reversing": None}
        evidence.append(record)
        location = f"{binding['parent']}:{binding['parent_drawable']} → {binding['child']}:{binding['child_drawable']}"
        try:
            for role, owner in owners.items():
                key, index = binding[role], binding[role + "_drawable"]
                if etree.fromstring(documents[key][0], etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True)).tag == "Fragment" or owner.find("Matrix") is not None:
                    raise LookupError("Fragment/drawable matrix and physics pose precedence is outside this declared bind-frame check")
                rig = selected_rigs.get((key, index))
                if rig is not None:
                    animation_model.compatible_skeleton(owner, rig)
                    embedded = owner.find("Skeleton")
                    if embedded is not None:
                        owner.remove(embedded)
                    owner.append(deepcopy(rig.find("Skeleton")))
                record[role + "_anchor_matrix"] = (attachment_validation.anchor(owner, binding[role + "_bone"])["skeleton_matrix"]
                    if binding[role + "_bone"] else deepcopy(IDENTITY))
            target = multiply(record["parent_anchor_matrix"], local)
            placement = multiply(target, inverse(record["child_anchor_matrix"]))
            inverse(placement)  # Check conditioning and nonsingularity of the assembled result too.
            rebuilt = multiply(placement, record["child_anchor_matrix"])
            error = max(abs(rebuilt[r][c]-target[r][c]) for r in range(4) for c in range(4)) / max(1, max(abs(v) for row in target for v in row))
            if error > 1e-7:
                raise ValueError("Declared anchors do not coincide within the supported numeric tolerance")
            record.update(status="checked", child_to_parent_matrix=[list(row) for row in placement],
                          relative_anchor_error=error, orientation_reversing=determinant(placement) < 0,
                          message="Declared anchors align in the parent's bind space; engine placement remains unverified.")
            add("attachments", "warning" if record["orientation_reversing"] else "pass", "declared_assembly_checked", location,
                record["message"] + (" Placement reverses orientation; winding/material behavior needs review." if record["orientation_reversing"] else ""))
        except (ValueError, LookupError, TypeError) as exc:
            record["status"] = "not_checked" if isinstance(exc, LookupError) else "fail"
            record["message"] = str(exc)
            add("attachments", record["status"], "declared_assembly_unresolved", location, str(exc))
    return evidence
