import hashlib
import json
import os
from pathlib import Path

from lxml import etree
import pytest

from allin1_sdk import animation_model

FIXTURE = Path(__file__).parent / "fixtures/animation_skin.ydr.xml"


def test_palette_mapping_byte_weights_hierarchy_and_transport():
    packet = animation_model.analyze(FIXTURE.read_bytes())
    assert packet["selected"] == "0" and packet["lod"] == "High"
    assert [(b["index"], b["tag"], b["parent"]) for b in packet["bones"]] == [(0, 0, -1), (1, 42, 0)]
    mesh = packet["meshes"][0]
    assert mesh["indices"] == [[0,0,0,0, 1,0,0,0, 1,0,0,0]]
    assert mesh["weights"][0][-4:] == [128/255, 127/255, 0, 0]
    from allin1_sdk.desktop_protocol import _bounded
    assert _bounded({"session": {"animation_model": packet}})["session"]["animation_model"] == packet
    assert json.loads((FIXTURE.parents[2] / "desktop/src/nativeAnimationModelFixture.json").read_text()) == packet


def test_dictionary_requires_explicit_owner_and_lod():
    drawable = etree.fromstring(FIXTURE.read_bytes())
    drawable.tag = "Item"
    dictionary = b"<DrawableDictionary>" + etree.tostring(drawable)*2 + b"</DrawableDictionary>"
    packet = animation_model.analyze(dictionary)
    assert packet["selected"] is None and packet["bones"] == [] and len(packet["drawables"]) == 2
    assert animation_model.analyze(dictionary, "1")["bones"][1]["tag"] == 42
    for selection in ["2", "../0", 0, [], "01"]:
        with pytest.raises(ValueError, match="exact drawable"):
            animation_model.analyze(dictionary, selection)
    with pytest.raises(ValueError, match="LOD"):
        animation_model.analyze(dictionary, "0", "Low")


@pytest.mark.parametrize("old,new,message", [
    ('Tag value="42"', 'Tag value="0"', "unique"),
    ('Index value="1"', 'Index value="2"', "array order"),
    ('ParentIndex value="0"', 'ParentIndex value="1"', "cycle"),
    ('ParentIndex value="0"', 'ParentIndex value="10"', "range"),
    ('Scale x="1"', 'Scale x="0"', "singular"),
    ('Translation x="0"', 'Translation x="NaN"', "non-finite"),
    ('Rotation x="0"', 'Rotation x="1e308"', "numeric limits"),
    ('<BoneIDs>1, 0', '<BoneIDs>7, 0', "palette"),
    ('255 0 0 0    1', '255 0 0 0    7', "palette entry"),
    ('255 0 0 0', '0 0 0 0', "byte weights"),
    ('255 0 0 0', '1.0 0 0 0', "decoded bytes"),
    ('<BlendIndices/>', '<Unsupported/>', "semantic"),
    ('<Data>0 1 2</Data>', '<Data>0 1 9</Data>', "missing vertex"),
    ('<HasSkin value="1"', '<HasSkin value="2"', "range"),
])
def test_reject_ambiguous_or_invalid_model_bindings(old, new, message):
    with pytest.raises(ValueError, match=message):
        animation_model.analyze(FIXTURE.read_text().replace(old, new).encode())


def test_bounded_xml_and_explicit_file(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="DTD"):
        animation_model.analyze(b'<!DOCTYPE Drawable [<!ENTITY x SYSTEM "file:///secret">]><Drawable>&x;</Drawable>')
    with pytest.raises(ValueError, match="Invalid model XML"):
        animation_model.analyze(b"<broken>")
    with pytest.raises(ValueError, match="Expected"):
        animation_model.analyze(b"<Skeleton/>")
    binary = tmp_path / "fixture.ydr"
    binary.write_bytes(b"not XML")
    with pytest.raises(ValueError, match="exported"):
        animation_model.inspect(str(binary))
    before = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert animation_model.inspect(str(FIXTURE))["source_sha256"] == before
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == before
    monkeypatch.setattr(animation_model, "MAX_XML", 2)
    with pytest.raises(ValueError, match="16 MiB"):
        animation_model.inspect(str(FIXTURE))
    with pytest.raises(ValueError, match="16 MiB"):
        animation_model.analyze(b"<Drawable/>")


def test_geometry_limits_retain_lod_selection_without_truncated_meshes(monkeypatch):
    monkeypatch.setattr(animation_model, "MAX_VERTICES", 2)
    packet = animation_model.analyze(FIXTURE.read_bytes())
    assert "lower LOD" in packet["view_unavailable"]
    assert packet["lods"] == ["High"] and packet["selected"] == "0"
    assert packet["meshes"] == [] and packet["vertex_count"] == 0


def test_large_vertex_buffers_survive_report_chunk_limits():
    root = etree.fromstring(FIXTURE.read_bytes())
    geometry = root.find("DrawableModelsHigh/Item/Geometries/Item")
    geometry.find("VertexBuffer/Data").text = "\n".join(["0 0 0 255 0 0 0 1 0 0 0 0 0 1 0 0"]*2100)
    geometry.find("IndexBuffer/Data").text = " ".join(str(i) for i in range(2100))
    packet = animation_model.analyze(etree.tostring(root))
    from allin1_sdk.desktop_protocol import _bounded
    assert _bounded({"session": {"animation_model": packet}})["session"]["animation_model"] == packet
    assert packet["vertex_count"] == 2100 and packet["triangle_count"] == 700
    assert sum(map(len, packet["meshes"][0]["positions"])) == 6300


def test_rigid_mesh_uses_its_model_bone_and_fragment_uses_only_primary_drawable():
    root = etree.fromstring(FIXTURE.read_bytes())
    root.find("DrawableModelsHigh/Item/HasSkin").set("value", "0")
    root.find("DrawableModelsHigh/Item/BoneIndex").set("value", "1")
    fragment = etree.Element("Fragment")
    fragment.append(root)
    packet = animation_model.analyze(etree.tostring(fragment))
    assert packet["meshes"][0]["skin"] is False
    assert packet["meshes"][0]["rigid_bone"] == 1
    assert packet["meshes"][0]["indices"] == []


def test_shared_skeleton_is_explicit_and_independently_hash_bound(tmp_path):
    root = etree.fromstring(FIXTURE.read_bytes())
    root.remove(root.find("Skeleton"))
    mesh_xml = etree.tostring(root)
    missing = animation_model.analyze(mesh_xml)
    assert "no skeleton" in missing["binding_required"]
    assert missing["bones"] == [] and missing["meshes"] == []
    bound = animation_model.analyze(mesh_xml, skeleton_data=FIXTURE.read_bytes())
    assert "binding_required" not in bound
    assert bound["skeleton_binding"]["source_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert bound["source_sha256"] == hashlib.sha256(mesh_xml).hexdigest()
    original = animation_model.analyze(FIXTURE.read_bytes())
    assert bound["bones"] == original["bones"] and bound["meshes"] == original["meshes"]
    source = tmp_path / "mesh.ydd.xml"
    source.write_bytes(mesh_xml)
    inspected = animation_model.inspect(str(source), skeleton_xml=str(FIXTURE))
    assert inspected["skeleton_binding"]["source"] == str(FIXTURE.resolve())
    assert source.read_bytes() == mesh_xml
    from allin1_sdk.desktop_protocol import _bounded
    assert _bounded({"session": {"animation_model": inspected}})["session"]["animation_model"] == inspected


def test_shared_dictionary_requires_its_own_exact_selection():
    root = etree.fromstring(FIXTURE.read_bytes())
    root.tag = "Item"
    dictionary = b"<DrawableDictionary>" + etree.tostring(root)*2 + b"</DrawableDictionary>"
    root.tag = "Drawable"
    root.remove(root.find("Skeleton"))
    mesh = etree.tostring(root)
    pending = animation_model.analyze(mesh, skeleton_data=dictionary)
    assert pending["skeleton_binding"]["selected"] is None
    assert "Choose an exact" in pending["binding_required"]
    selected = animation_model.analyze(mesh, skeleton_data=dictionary, skeleton_drawable="1")
    assert selected["skeleton_binding"]["selected"] == "1" and len(selected["bones"]) == 2
    with pytest.raises(ValueError, match="exact drawable"):
        animation_model.analyze(mesh, skeleton_data=dictionary, skeleton_drawable="2")
    with pytest.raises(ValueError, match="before its drawable"):
        animation_model.analyze(mesh, skeleton_drawable="0")


@pytest.mark.parametrize("old,new", [('Tag value="42"', 'Tag value="43"'), ('Translation x="0"', 'Translation x="3"')])
def test_reject_conflicting_embedded_and_shared_bind_skeletons(old, new):
    with pytest.raises(ValueError, match="conflicts"):
        animation_model.analyze(FIXTURE.read_bytes(), skeleton_data=FIXTURE.read_text().replace(old, new).encode())


def test_invalid_shared_skeleton_is_not_silently_replaced_with_embedded():
    root = etree.fromstring(FIXTURE.read_bytes())
    root.remove(root.find("Skeleton"))
    with pytest.raises(ValueError, match="1–512"):
        animation_model.analyze(FIXTURE.read_bytes(), skeleton_data=etree.tostring(root))
    with pytest.raises(ValueError, match="DTD"):
        animation_model.analyze(FIXTURE.read_bytes(), skeleton_data=b'<!DOCTYPE Drawable><Drawable/>')
    with pytest.raises(ValueError, match="absolute path"):
        animation_model.inspect(str(FIXTURE), skeleton_xml=False)


@pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST") != "1", reason="Native helper opt-in")
@pytest.mark.parametrize("edition", ["Legacy", "Enhanced"])
def test_native_model_decode_retains_palette_and_skin_data(tmp_path, edition):
    from allin1_sdk.native_assets import NativeAssetInspector
    from allin1_sdk.processes import run_hidden
    inspector = NativeAssetInspector(FIXTURE.parents[2])
    source = tmp_path / "skin.ydr"
    result = run_hidden([str(inspector.patcher), "asset-from-xml", str(FIXTURE), str(source), str(tmp_path), "legacy" if edition == "Legacy" else "gen9"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    report = inspector.inspect_bytes(source.name, source.read_bytes(), edition=edition)
    assert report.structured_text
    decoded = animation_model.analyze(report.structured_text.encode())
    original = animation_model.analyze(FIXTURE.read_bytes())
    assert decoded["bones"] == original["bones"]
    assert decoded["meshes"] == original["meshes"]
    external = etree.fromstring(FIXTURE.read_bytes())
    external.remove(external.find("Skeleton"))
    external_path = tmp_path / "external.ydr.xml"
    external_path.write_bytes(etree.tostring(external))
    external_source = tmp_path / "external.ydr"
    result = run_hidden([str(inspector.patcher), "asset-from-xml", str(external_path), str(external_source), str(tmp_path), "legacy" if edition == "Legacy" else "gen9"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    external_report = inspector.inspect_bytes(external_source.name, external_source.read_bytes(), edition=edition)
    mesh_data = external_report.structured_text.encode()
    assert "binding_required" in animation_model.analyze(mesh_data)
    shared = animation_model.analyze(mesh_data, skeleton_data=report.structured_text.encode())
    assert shared["bones"] == decoded["bones"] and shared["meshes"] == decoded["meshes"]
