from copy import deepcopy
import hashlib
import json
from pathlib import Path

from lxml import etree
import pytest

from allin1_sdk.asset_validation import analyze
from allin1_sdk.desktop_protocol import _bounded

FIXTURE = Path(__file__).parent / "fixtures/animation_skin.ydr.xml"


def report(mutator=None):
    root = etree.fromstring(FIXTURE.read_bytes())
    if mutator:
        mutator(root)
    return analyze(etree.tostring(root))


def check(value, category):
    return next(c for c in value["checks"] if c["category"] == category)


def test_empty_shader_slot_is_unknown_not_a_proven_missing_dependency():
    def mutate(root):
        parameters = root.find("ShaderGroup/Shaders/Item/Parameters")
        if parameters is None:
            group = etree.SubElement(root, "ShaderGroup")
            parameters = etree.SubElement(etree.SubElement(etree.SubElement(group, "Shaders"), "Item"), "Parameters")
        etree.SubElement(parameters, "Item", name="BumpSampler", type="Texture")
    value = report(mutate)
    findings = [f for f in check(value, "textures")["findings"] if f["code"] == "unbound_texture_slot"]
    assert findings and all(f["status"] == "not_checked" for f in findings)


@pytest.mark.parametrize("value",["nan","inf","1e39","unknown"])
def test_invalid_authored_lod_distance_is_not_silently_accepted(value):
    result=report(lambda root:root.find("LodDistHigh").set("value",value))
    assert check(result,"lods")["status"]=="fail"
    assert result["lod_distances"][0]["distance"] is None


def test_authored_lod_thresholds_and_missing_or_nonincreasing_values_are_explicit():
    original=report()
    assert original["lod_distances"][0]=={"drawable":0,"lod":"High","field":"LodDistHigh","distance":100.0,"status":"valid_authored_value","models_present":True}
    assert original["lod_distances"][1]["models_present"] is False
    def mutate(root):
        lower=deepcopy(root.find("DrawableModelsHigh"));lower.tag="DrawableModelsMedium";root.append(lower)
        etree.SubElement(root,"LodDistMed",value="50")
    result=report(mutate)
    assert any(f["code"]=="nonincreasing_lod_distance" for f in check(result,"lods")["findings"])
    assert result["runtime_status"]=="not_tested"
    missing=report(lambda root:root.remove(root.find("LodDistHigh")))
    assert any(f["code"]=="lod_distance_missing" for f in check(missing,"lods")["findings"])
    negative=report(lambda root:root.find("LodDistHigh").set("value","-1"))
    assert any(f["code"]=="negative_lod_distance" and f["status"]=="warning" for f in check(negative,"lods")["findings"])
    assert negative["lod_distances"][0]["distance"]==-1


def test_valid_decoding_is_not_an_all_clear_and_report_identity_is_exact():
    value = report()
    assert value["static_status"] == "incomplete" and value["runtime_status"] == "not_tested"
    assert check(value, "skeleton")["status"] == "pass"
    assert check(value, "skinning")["status"] == "pass"
    for key in ("attachments", "metadata", "textures"):
        assert check(value, key)["status"] == "not_checked"
    assert value["lod_metrics"] == [{"drawable": 0, "lod": "High", "vertices": 3, "triangles": 1, "complete": True}]
    identity = value.pop("report_sha256")
    assert identity == hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert _bounded(value) == value


@pytest.mark.parametrize("field,value", [("Tag", "0"), ("ParentIndex", "1"), ("Index", "0")])
def test_ambiguous_and_cyclic_skeletons_fail(field, value):
    result = report(lambda root: root.find(f"Skeleton/Bones/Item[2]/{field}").set("value", value))
    assert check(result, "skeleton")["status"] == "fail"
    assert result["static_status"] == "fail"


@pytest.mark.parametrize("field,axis,value", [("Rotation", "w", "0"), ("Scale", "x", "0"), ("Translation", "x", "nan")])
def test_invalid_transforms_are_reported_not_silently_normalized(field, axis, value):
    result = report(lambda root: root.find(f"Skeleton/Bones/Item/{field}").set(axis, value))
    assert check(result, "skeleton")["status"] == "fail"


def test_skin_palette_and_texture_ambiguity_fail():
    def mutate(root):
        root.find(".//BoneIDs").text = "999"
        textures = etree.SubElement(root.find("ShaderGroup"), "TextureDictionary")
        for _ in range(2):
            etree.SubElement(etree.SubElement(textures, "Item"), "Name").text = "fixture_diffuse"
    value = report(mutate)
    assert check(value, "skinning")["status"] == "fail"
    assert check(value, "textures")["status"] == "fail"


def test_ineffective_lod_and_geometry_errors_are_visible():
    def mutate(root):
        lower = deepcopy(root.find("DrawableModelsHigh"))
        lower.tag = "DrawableModelsLow"
        root.append(lower)
    value = report(mutate)
    assert any(f["code"] == "ineffective_lod" for f in check(value, "lods")["findings"])
    bad = report(lambda root: setattr(root.find(".//VertexBuffer/Data"), "text", "nan 0 0"))
    assert check(bad, "lods")["status"] == "fail"


def test_missing_rig_is_unknown_not_a_pass_or_inferred_rig():
    value = report(lambda root: root.remove(root.find("Skeleton")))
    assert check(value, "skeleton")["status"] == check(value, "skinning")["status"] == "not_checked"


def test_multiple_drawable_owners_are_not_combined():
    root = etree.Element("DrawableDictionary")
    for _ in range(2):
        child = etree.fromstring(FIXTURE.read_bytes())
        child.tag = "Item"
        root.append(child)
    value = analyze(etree.tostring(root))
    assert check(value, "skeleton")["status"] == "warning"
    assert check(value, "skinning")["status"] == "pass"
    assert len(value["lod_metrics"]) == 2


def test_dtd_and_oversized_inputs_are_rejected():
    for value in (b'<!DOCTYPE Drawable [<!ENTITY x SYSTEM "file:///canary">]><Drawable/>', b"x" * (16*1024**2+1)):
        with pytest.raises(ValueError):
            analyze(value)
