from copy import deepcopy
import hashlib
import os
from pathlib import Path

from lxml import etree
import pytest

from allin1_sdk import asset_validation, package_validation
from allin1_sdk.desktop_protocol import _bounded


def fragment():
    primary = etree.fromstring((Path(__file__).parent / "fixtures/animation_skin.ydr.xml").read_bytes())
    root = etree.Element("Fragment")
    root.append(primary)
    lod = etree.SubElement(etree.SubElement(root, "Physics"), "LOD1")
    etree.SubElement(lod, "PositionOffset", x="2", y="3", z="4")
    etree.SubElement(etree.SubElement(lod, "Transforms"), "Item").text = "1 0 0 0 0 1 0 0 0 0 1 0 5 6 7 1"
    group = etree.SubElement(etree.SubElement(lod, "Groups"), "Item")
    etree.SubElement(group, "Name").text = "fixture_child"
    etree.SubElement(group, "ParentIndex", value="255")
    child = etree.SubElement(etree.SubElement(lod, "Children"), "Item")
    etree.SubElement(child, "GroupIndex", value="0")
    etree.SubElement(child, "BoneTag", value="42")
    drawable = deepcopy(primary)
    drawable.remove(drawable.find("Skeleton"))
    drawable.remove(drawable.find("ShaderGroup"))
    etree.SubElement(drawable, "Matrix").text = "1 0 0 0 1 0 0 0 1 8 9 10"
    child.append(drawable)
    return root


def findings(value):
    return [f for check in value["checks"] for f in check["findings"]]


def test_fragment_child_inherits_exact_owner_and_keeps_authored_frames_separate():
    root = fragment()
    data = etree.tostring(root)
    value = asset_validation.analyze(data)
    assert etree.tostring(root) == data
    assert value["source_sha256"] == hashlib.sha256(data).hexdigest()
    row = value["fragment_children"][0]
    assert row["bone_tag"] == 42 and row["bone_indices"] == [1]
    assert row["physics_matrix"][3] == [5, 6, 7, 1]
    assert row["position_offset"] == [2, 3, 4]
    assert row["drawables"][0]["matrix"][3] == [8, 9, 10]
    assert row["drawables"][0]["lod_metrics"][0]["triangles"] == 1
    assert not any(f["status"] == "fail" for f in findings(value))
    assert not any(f["code"] == "fragment_shared_skeleton_required" for f in findings(value))
    assert value["runtime_status"] == "not_tested"
    _bounded(value)


@pytest.mark.parametrize("path,value,code", [
    ("Physics/LOD1/Children/Item/GroupIndex", "2", "fragment_group_unresolved"),
    ("Physics/LOD1/Groups/Item/ParentIndex", "0", "fragment_group_hierarchy"),
    ("Physics/LOD1/Children/Item/BoneTag", "65536", "fragment_child_reference"),
])
def test_invalid_fragment_indices_are_reported(path, value, code):
    root = fragment()
    root.find(path).set("value", value)
    result = asset_validation.analyze(etree.tostring(root))
    assert any(f["code"] == code and f["status"] == "fail" for f in findings(result))


@pytest.mark.parametrize("raw", ["nan " + "0 " * 15, "1 2 3", "inf " + "0 " * 15])
def test_nonfinite_or_malformed_physics_matrix_is_not_defaulted(raw):
    root = fragment()
    root.find("Physics/LOD1/Transforms/Item").text = raw
    result = asset_validation.analyze(etree.tostring(root))
    assert result["fragment_children"][0]["physics_matrix"] is None
    assert any(f["code"] == "fragment_transform_invalid" and f["status"] == "fail" for f in findings(result))


def test_null_children_keep_array_indices_and_unmapped_tags_stay_unknown():
    root = fragment()
    children = root.find("Physics/LOD1/Children")
    children.insert(0, etree.Element("Item"))
    children[1].find("BoneTag").set("value", "65535")
    result = asset_validation.analyze(etree.tostring(root))
    assert result["fragment_children"][0]["null_child"]
    assert result["fragment_children"][1]["child_index"] == 1
    assert result["fragment_children"][1]["physics_matrix"] is None
    assert any(f["code"] == "fragment_bone_unresolved" and f["status"] == "not_checked" for f in findings(result))
    assert any(f["code"] == "fragment_transform_count" for f in findings(result))


def test_child_skin_palette_and_conflicting_embedded_rig_are_not_skipped():
    root = fragment()
    drawable = root.find("Physics/LOD1/Children/Item/Drawable")
    drawable.find("DrawableModelsHigh/Item/Geometries/Item/BoneIDs").text = "999, 0"
    result = asset_validation.analyze(etree.tostring(root))
    assert any(f["code"] == "fragment_skin_binding" and f["status"] == "fail" for f in findings(result))
    root = fragment()
    skeleton = deepcopy(root.find("Drawable/Skeleton"))
    skeleton.find("Bones/Item/Tag").set("value", "99")
    root.find("Physics/LOD1/Children/Item/Drawable").append(skeleton)
    result = asset_validation.analyze(etree.tostring(root))
    assert any(f["code"] == "fragment_child_binding" and f["status"] == "fail" for f in findings(result))


def test_explicit_shared_primary_rig_also_supplies_fragment_children():
    root = fragment()
    owner = deepcopy(root.find("Drawable"))
    root.find("Drawable").remove(root.find("Drawable/Skeleton"))
    result = asset_validation.analyze(etree.tostring(root), rig_owners={0: owner})
    assert result["fragment_children"][0]["bone_indices"] == [1]
    assert not any(f["code"] == "fragment_shared_skeleton_required" for f in findings(result))


def test_bounds_and_duplicate_lods_cannot_report_children_as_checked():
    root = fragment()
    root.find("Physics").append(deepcopy(root.find("Physics/LOD1")))
    value = asset_validation.analyze(etree.tostring(root))
    assert not value["fragment_children"]
    assert any(f["code"] == "fragment_duplicate_lod" and f["status"] == "fail" for f in findings(value))
    root = fragment()
    children = root.find("Physics/LOD1/Children")
    for _ in range(64):
        etree.SubElement(children, "Item")
    value = asset_validation.analyze(etree.tostring(root))
    assert not value["fragment_children"]
    assert any(f["code"] == "fragment_child_limit" and f["status"] == "not_checked" for f in findings(value))


def test_duplicate_arrays_and_missing_group_names_are_not_normalized():
    root = fragment()
    root.find("Physics/LOD1").append(etree.Element("Children"))
    value = asset_validation.analyze(etree.tostring(root))
    assert not value["fragment_children"]
    assert any(f["code"] == "fragment_duplicate_array" for f in findings(value))
    root = fragment()
    group = root.find("Physics/LOD1/Groups/Item")
    group.remove(group.find("Name"))
    value = asset_validation.analyze(etree.tostring(root))
    assert any(f["code"] == "fragment_group_hierarchy" and f["status"] == "fail" for f in findings(value))


def test_package_keeps_fragment_failures_and_exact_source_provenance(tmp_path):
    root = fragment()
    root.find("Physics/LOD1/Children/Item/GroupIndex").set("value", "2")
    path = tmp_path / "fixture.yft.xml"
    original = etree.tostring(root)
    path.write_bytes(original)
    value = package_validation.inspect(tmp_path)
    assert value["fragment_children"][0]["source"] == "fixture.yft.xml"
    assert any(f["code"] == "fragment_group_unresolved" and f["status"] == "fail" for f in findings(value))
    assert path.read_bytes() == original
    assert value["static_status"] == "fail"
    _bounded(value)


@pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST") != "1", reason="explicit native helper gate")
@pytest.mark.parametrize("edition", ["Legacy", "Enhanced"])
def test_native_fragment_roundtrip_exposes_child_owner_and_matrix_values(tmp_path, edition):
    from allin1_sdk.paths import project_root
    from allin1_sdk.processes import run_hidden
    root = fragment()
    etree.SubElement(root, "Name").text = "sdk_fragment_fixture"
    xml = tmp_path / "fixture.yft.xml"
    xml.write_bytes(etree.tostring(root))
    package = tmp_path / "package"
    package.mkdir()
    target = package / "fixture.yft"
    helper = project_root() / "tools/RpfPatcher/RpfPatcher.exe"
    result = run_hidden([str(helper), "asset-from-xml", str(xml), str(target), str(tmp_path),
                         "legacy" if edition == "Legacy" else "gen9"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr or result.stdout
    value = package_validation.inspect(package, edition=edition)
    assert value["fragment_children"], value["checks"]
    child = value["fragment_children"][0]
    assert child["bone_indices"] == [1]
    assert child["physics_matrix"][3] == [5, 6, 7, 1]
    assert child["drawables"][0]["matrix"][3] == [8, 9, 10]
    assert child["drawables"][0]["lod_metrics"][0]["triangles"] == 1
    assert not any(f["status"] == "fail" for f in findings(value)), findings(value)
