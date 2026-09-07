import hashlib
import json
import math
from pathlib import Path

from lxml import etree
import pytest

from allin1_sdk import package_validation, optimization_package
from allin1_sdk.assembly_validation import inverse
from test_attachment_validation import package_fixture
from test_artifact_identity import build
from test_optimization_package import request


def binding(root, *, parent="body.ydr.xml", child="clip.ydr.xml", child_bone="tip"):
    return {"parent": "package:" + parent, "parent_drawable": 0,
            "parent_sha256": hashlib.sha256((root / parent).read_bytes()).hexdigest(), "parent_bone": "tip",
            "child": "package:" + child, "child_drawable": 0,
            "child_sha256": hashlib.sha256((root / child).read_bytes()).hexdigest(), "child_bone": child_bone,
            "offset": [0, 0, 0]}


def test_declared_bone_alignment_includes_parent_rotation_scale_and_child_inverse(tmp_path):
    root = package_fixture(tmp_path / "package")
    document = etree.parse(str(root / "body.ydr.xml"))
    bone = document.find("Skeleton/Bones/Item")
    bone.find("Rotation").set("x", "1"); bone.find("Rotation").set("w", "0")
    bone.find("Scale").set("z", "2")
    (root / "body.ydr.xml").write_bytes(etree.tostring(document))
    declaration = binding(root)
    declaration["offset"] = [3, 0, 1]
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    report = package_validation.inspect(root, assembly_bindings=[declaration])
    row = report["assembly_evidence"][0]
    assert row["status"] == "checked"
    assert row["child_to_parent_matrix"] == [[1, 0, 0, 3], [0, -1, 0, 0], [0, 0, -2, -2], [0, 0, 0, 1]]
    assert row["relative_anchor_error"] == 0
    assert row["orientation_reversing"] is False
    assert report["runtime_status"] == "not_tested"
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before


def test_model_origin_is_explicit_not_a_guessed_child_bone(tmp_path):
    root = package_fixture(tmp_path / "package")
    row = package_validation.inspect(root, assembly_bindings=[binding(root, child_bone="")])["assembly_evidence"][0]
    assert row["child_to_parent_matrix"][2][3] == 1
    assert row["status"] == "checked"


def test_reflected_assembly_is_warning_not_placement_certification(tmp_path):
    root = package_fixture(tmp_path / "package")
    document = etree.parse(str(root / "body.ydr.xml"))
    document.find("Skeleton/Bones/Item/Scale").set("x", "-1")
    (root / "body.ydr.xml").write_bytes(etree.tostring(document))
    report = package_validation.inspect(root, assembly_bindings=[binding(root)])
    row = report["assembly_evidence"][0]
    assert row["status"] == "checked" and row["orientation_reversing"] is True
    findings = [f for check in report["checks"] for f in check["findings"] if f["code"] == "declared_assembly_checked"]
    assert len(findings) == 1 and findings[0]["status"] == "warning"
    assert report["runtime_status"] == "not_tested"


@pytest.mark.parametrize("variant", ["stale", "owner", "self", "duplicate", "count", "offset", "bool", "unknown"])
def test_invalid_or_stale_declarations_block_report(tmp_path, variant):
    root = package_fixture(tmp_path / "package")
    row = binding(root); rows = [row]
    if variant == "stale": row["child_sha256"] = "0" * 64
    if variant == "owner": row["child_drawable"] = 1
    if variant == "self": row.update(child=row["parent"], child_sha256=row["parent_sha256"])
    if variant == "duplicate": rows *= 2
    if variant == "count": rows *= 33
    if variant == "offset": row["offset"][0] = float("nan")
    if variant == "bool": row["offset"][0] = True
    if variant == "unknown": row["engine_approved"] = True
    with pytest.raises(ValueError): package_validation.inspect(root, assembly_bindings=rows)


def test_missing_bone_and_fragment_semantics_are_not_success(tmp_path):
    root = package_fixture(tmp_path / "package")
    row = binding(root, child_bone="absent")
    report = package_validation.inspect(root, assembly_bindings=[row])
    assert report["assembly_evidence"][0]["status"] == "fail"
    body = (root / "body.ydr.xml").read_bytes()
    (root / "body.ydr.xml").write_bytes(b"<Fragment>" + body.replace(b'<?xml version="1.0" encoding="utf-8"?>', b"") + b"</Fragment>")
    row = binding(root)
    report = package_validation.inspect(root, assembly_bindings=[row])
    assert report["assembly_evidence"][0]["status"] == "not_checked"


def test_singular_or_ill_conditioned_frame_is_rejected():
    for scale in (0, 1e-14):
        with pytest.raises(ValueError): inverse([[scale, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]])


def test_local_rotation_precedes_parent_axis_translation_and_child_inverse(tmp_path):
    root = package_fixture(tmp_path / "package")
    declaration = binding(root)
    declaration.update(offset=[2,3,4], rotation=[math.sqrt(.5),0,0,math.sqrt(.5)])
    report = package_validation.inspect(root, assembly_bindings=[declaration])
    row = report["assembly_evidence"][0]
    expected = [[1,0,0,2],[0,0,-1,4],[0,1,0,5],[0,0,0,1]]
    for actual, values in zip(row["child_to_parent_matrix"], expected):
        assert actual == pytest.approx(values, abs=1e-12)
    assert row["status"] == "checked" and row["relative_anchor_error"] < 1e-12
    assert row["rotation"] == declaration["rotation"]
    assert report["runtime_status"] == "not_tested"


@pytest.mark.parametrize("rotation", [None, [], [0,0,1], [0,0,0,0], [0,0,0,2],
    [0,0,.5,.5], [True,0,0,0], [0,0,0,float("nan")], [0,0,0,"1"]])
def test_invalid_rotation_does_not_silently_normalize_or_default(tmp_path, rotation):
    root = package_fixture(tmp_path / "package")
    declaration = {**binding(root), "rotation": rotation}
    with pytest.raises(ValueError, match="unit XYZW"):
        package_validation.inspect(root, assembly_bindings=[declaration])


@pytest.mark.parametrize("rotation", [None, [1,0,0,0]])
def test_optimization_retains_identical_declared_assembly_before_and_after(tmp_path, monkeypatch, rotation):
    monkeypatch.setattr(optimization_package.artifact_identity, "current", build)
    payload = request(tmp_path)
    root = Path(payload["source"])
    (root / "clip.ydr.xml").write_bytes((root / "car.ydr.xml").read_bytes())
    (root / "clip.meta").write_text('<CVehicleModelInfo__InitDataList><InitDatas><Item><modelName>clip</modelName><txdName>paint</txdName></Item></InitDatas></CVehicleModelInfo__InitDataList>')
    declaration = binding(root, parent="car.ydr.xml")
    if rotation is not None:
        declaration["rotation"] = rotation
    payload["settings"]["assembly_bindings"] = [declaration]
    value = optimization_package.inspect(payload)
    assert value["before_report"]["assembly_evidence"] == value["after_report"]["assembly_evidence"]
    assert value["before_report"]["assembly_evidence"][0]["status"] == "checked"
    output = tmp_path / "export"
    optimization_package.apply({**payload, "action": "export", "destination": str(output), "expected_state_sha256": value["state_sha256"]})
    receipt = json.loads((output / "optimization.json").read_bytes())
    assert receipt["before_report"]["assembly_evidence"] == receipt["after_report"]["assembly_evidence"]
    assert (output / "originals/car.ydr.xml").read_bytes() == (root / "car.ydr.xml").read_bytes()
    declaration["rotation"] = [0,1,0,0]
    with pytest.raises(ValueError, match="changed"):
        optimization_package.apply({**payload,"action":"export","destination":str(tmp_path / "stale"),"expected_state_sha256":value["state_sha256"]})
    assert not (tmp_path / "stale").exists()


def test_declared_assembly_uses_only_explicit_compatible_shared_rig(tmp_path):
    root = package_fixture(tmp_path / "package")
    comparison = tmp_path / "context"; comparison.mkdir()
    rig = comparison / "rig.ydr.xml"
    rig.write_bytes((root / "clip.ydr.xml").read_bytes())
    child = etree.parse(str(root / "clip.ydr.xml"))
    child.getroot().remove(child.find("Skeleton"))
    (root / "clip.ydr.xml").write_bytes(etree.tostring(child))
    declaration = binding(root)
    unbound = package_validation.inspect(root, comparison=comparison, assembly_bindings=[declaration])
    assert unbound["assembly_evidence"][0]["status"] == "not_checked"
    selected = {"model": "package:clip.ydr.xml", "drawable": 0, "rig": "comparison:rig.ydr.xml", "rig_drawable": 0,
                "model_sha256": declaration["child_sha256"], "rig_sha256": hashlib.sha256(rig.read_bytes()).hexdigest()}
    report = package_validation.inspect(root, comparison=comparison, assembly_bindings=[declaration], rig_bindings=[selected])
    assert report["assembly_evidence"][0]["status"] == "checked"
    assert report["assembly_evidence"][0]["child_to_parent_matrix"][2][3] == 0
