import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from lxml import etree

from allin1_sdk import collision_primitives as shapes, workspace_desktop
from allin1_sdk.native_assets import NativeCollisionScene, _ModelGeometry, _collision_scene_from_xml

IDENTITY = "1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"


def standalone(kind):
    return f'<BoundsFile><Bounds type="{kind}"><SphereCenter x="0" y="0" z="0"/><SphereRadius value="3"/><Margin value="0.5"/><BoxMin x="-1" y="-2" z="-1"/><BoxMax x="1" y="2" z="1"/><MaterialIndex value="5"/></Bounds></BoundsFile>'


POLYGONS = '''<BoundsFile><Bounds type="Composite"><Children><Item type="GeometryBVH">
<BoxMin x="-2" y="-2" z="-2"/><BoxMax x="4" y="4" z="4"/><BoxCenter x="1" y="1" z="1"/>
<SphereCenter x="1" y="1" z="1"/><SphereRadius value="5"/><Margin value="0.04"/>
<CompositeTransform>1 0 0 0 0 1 0 0 0 0 1 0 10 0 0 1</CompositeTransform>
<GeometryCenter x="0" y="0" z="0"/><Vertices>0,0,0
0,1,1
1,1,0
0,1,0
1,0,1
0,2,0</Vertices><Polygons>
<Triangle m="0" v1="0" v2="1" v3="4"/>
<Box m="0" v1="0" v2="1" v3="2" v4="4"/>
<Sphere m="0" v="0" radius="1"/>
<Capsule m="0" v1="0" v2="5" radius="0.5"/>
<Cylinder m="0" v1="0" v2="5" radius="0.5"/>
</Polygons><Materials><Item><Type value="0"/><ProceduralID value="0"/><RoomID value="0"/><PedDensity value="0"/><MaterialColourIndex value="0"/><Flags>NONE</Flags></Item></Materials>
</Item></Children></Bounds></BoundsFile>'''


def parse(tmp_path, xml):
    path = tmp_path / "asset.ybn.xml"
    path.write_text(xml, encoding="utf-8")
    return _collision_scene_from_xml(path, "asset.ybn")


def test_control_box_reconstructs_eight_corners_and_outward_faces():
    points, triangles = shapes.control_box([(0, 0, 0), (0, 1, 1), (1, 1, 0), (1, 0, 1)])
    assert len(set(points)) == 8 and len(triangles) == 12
    assert set(points) == {(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)}
    for face in triangles:
        a, b, c = [points[i] for i in face]
        normal = shapes.cross(shapes.sub(b, a), shapes.sub(c, a))
        assert sum(x*y for x, y in zip(normal, shapes.sub(a, (.5, .5, .5)))) > 0


@pytest.mark.parametrize("kind", ["sphere", "capsule", "cylinder"])
def test_curves_have_outward_faces_and_expected_extents(kind):
    start, end = (0, 0, 0), (0, 0, 2)
    points, triangles = shapes.curved(start, end, 1, sphere=kind == "sphere", capsule=kind == "capsule")
    assert min(p[0] for p in points) == pytest.approx(-1)
    assert max(p[0] for p in points) == pytest.approx(1)
    assert min(p[2] for p in points) == pytest.approx(0 if kind == "cylinder" else -1)
    assert max(p[2] for p in points) == pytest.approx({"sphere": 1, "capsule": 3, "cylinder": 2}[kind])
    for face in triangles:
        a, b, c = [points[i] for i in face]
        normal = shapes.cross(shapes.sub(b, a), shapes.sub(c, a))
        middle = tuple((x+y+z)/3 for x, y, z in zip(a, b, c))
        center = (0, 0, 0 if kind == "sphere" else 1)
        assert sum(x*y for x, y in zip(normal, shapes.sub(middle, center))) > 0


def test_transform_applies_child_then_parent_row_vector_matrices():
    root = etree.fromstring(b'<Bounds><CompositeTransform>2 0 0 0 0 3 0 0 0 0 4 0 0 5 0 1</CompositeTransform><Children><Item><CompositeTransform>1 0 0 0 0 1 0 0 0 0 1 0 10 0 0 1</CompositeTransform></Item></Children></Bounds>')
    assert shapes.transform([(1, 2, 3)], root.find("./Children/Item")) == ((22, 11, 12),)


def test_all_geometry_primitives_render_with_materials_and_transforms(tmp_path):
    scene, metadata, warning = parse(tmp_path, POLYGONS)
    assert warning is None
    assert dict(scene.rendered_primitive_counts) == {"Box": 1, "Capsule": 1, "Cylinder": 1, "Sphere": 1, "Triangle": 1}
    assert len(scene.geometries) == 5
    assert all(group.material_index == 0 for group in scene.geometries)
    assert scene.bounds["min"] == pytest.approx([9, -1, -1])
    assert scene.bounds["max"] == pytest.approx([11, 2.5, 1])
    assert metadata["collision_polygon_count"] == 5
    packet = shapes.packet(scene)
    assert packet["triangle_count"] == packet["displayed_triangles"]
    assert packet["truncated"] is False
    from allin1_sdk.desktop_protocol import _bounded
    assert _bounded({"session": {"collision": packet}})["session"]["collision"] == packet
    fixture = Path(__file__).resolve().parents[1] / "desktop/src/nativeCollisionFixture.json"
    assert json.loads(fixture.read_text()) == packet


@pytest.mark.parametrize("kind,low,high", [
    ("Box", [-1, -2, -1], [1, 2, 1]), ("Sphere", [-3, -3, -3], [3, 3, 3]),
    ("Capsule", [-.5, -3, -.5], [.5, 3, .5]), ("Cylinder", [-1, -2, -1], [1, 2, 1]),
    ("Disc", [-.5, -3, -3], [.5, 3, 3]),
])
def test_standalone_bound_shapes(kind, low, high, tmp_path):
    scene, _, warning = parse(tmp_path, standalone(kind))
    assert warning is None and scene is not None
    assert scene.bounds["min"] == pytest.approx(low)
    assert scene.bounds["max"] == pytest.approx(high)
    assert scene.geometries[0].material_index == 5


@pytest.mark.parametrize("old,new", [('radius="1"', 'radius="nan"'), ('v="0"', 'v="100"'),
    ('radius="1"', 'radius="-1"'), ('10 0 0 1</CompositeTransform>', '10 0 0 0</CompositeTransform>'),
    ('v1="0" v2="1" v3="2" v4="4"', 'v1="0" v2="0" v3="0" v4="0"')])
def test_invalid_geometry_fails_without_partial_success(tmp_path, old, new):
    scene, metadata, warning = parse(tmp_path, POLYGONS.replace(old, new))
    assert scene is None and metadata == {} and warning.startswith("Collision preview unavailable:")


def test_packet_caps_groups_and_faces_without_hiding_full_counts():
    geometry = _ModelGeometry(((0, 0, 0), (1, 0, 0), (0, 1, 0)), ((0, 1, 2),)*1000, "owner", component="Triangle mesh", material_index=0)
    scene = NativeCollisionScene("large.ybn", (geometry,)*120, (("Triangle", 120000),), 1, 120)
    packet = shapes.packet(scene)
    assert len(packet["groups"]) == 96 and packet["group_count"] == 120
    assert 0 < packet["displayed_triangles"] <= 1500
    assert all(group["triangles"] for group in packet["groups"])
    assert packet["triangle_count"] == 120000 and packet["truncated"]


@pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST") != "1", reason="native helper opt-in")
@pytest.mark.parametrize("edition", ["Legacy", "Enhanced"])
@pytest.mark.parametrize("kind", ["GeometryBVH", "Box", "Sphere", "Capsule", "Cylinder", "Disc"])
def test_native_ybn_source_workspace_and_rebuild_keep_collision_evidence(tmp_path, edition, kind):
    project = Path(__file__).resolve().parents[1]
    helper = project / "tools/RpfPatcher/RpfPatcher.exe"
    xml, source = tmp_path / "asset.ybn.xml", tmp_path / "asset.ybn"
    xml.write_text(POLYGONS if kind == "GeometryBVH" else standalone(kind), encoding="utf-8")
    generated = subprocess.run([str(helper), "asset-from-xml", str(xml), str(source), str(tmp_path), "legacy" if edition == "Legacy" else "gen9"], capture_output=True, text=True, timeout=60)
    assert generated.returncode == 0, generated.stderr or generated.stdout
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    context = {"module": "native", "source": str(source), "edition": edition}
    inspected = workspace_desktop.inspect(context)
    assert inspected["collision"]["primitive_counts"] == ({"Box": 1, "Capsule": 1, "Cylinder": 1, "Sphere": 1, "Triangle": 1} if kind == "GeometryBVH" else {kind: 1})
    if kind == "GeometryBVH":
        assert inspected["collision"]["bounds"]["min"][0] == pytest.approx(9, abs=.001)
    def perform(context, action, destination):
        request = {**context, "action": action, "destination": str(destination), "expected_state_sha256": workspace_desktop.inspect(context)["state_sha256"]}
        review = workspace_desktop.review(request)
        return workspace_desktop.apply({**request, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    exported = perform(context, "export", tmp_path / "workspace")
    assert exported["session"]["collision"] == inspected["collision"]
    perform({"module": "native", "workspace": str(tmp_path / "workspace")}, "build", tmp_path / "rebuilt.ybn")
    rebuilt = workspace_desktop.inspect({**context, "source": str(tmp_path / "rebuilt.ybn")})
    before, after = inspected["collision"], rebuilt["collision"]
    assert {k: v for k, v in before.items() if k not in {"groups", "bounds"}} == {k: v for k, v in after.items() if k not in {"groups", "bounds"}}
    for original, candidate in zip(before["groups"], after["groups"], strict=True):
        assert {k: v for k, v in original.items() if k != "triangles"} == {k: v for k, v in candidate.items() if k != "triangles"}
        for face, other in zip(original["triangles"], candidate["triangles"], strict=True):
            for point, restored in zip(face, other, strict=True):
                # Signed 16-bit vertices can move by one quantum on re-encoding.
                assert restored == pytest.approx(point, abs=.0002)
    for key, values in before["bounds"].items():
        assert after["bounds"][key] == pytest.approx(values, abs=.0002)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
