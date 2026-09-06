import hashlib
import json
import os
from pathlib import Path

import pytest
from lxml import etree

from allin1_sdk import native_relationships as relationships, workspace_desktop
from test_native_workspace_desktop import workspace

YMAP = b'''<CMapData><name>test_map</name><parent>parent_map</parent><entities>
<Item type="CEntityDef"><archetypeName>unpositioned</archetypeName><parentIndex value="-1"/></Item>
<Item type="CEntityDef"><archetypeName>chair</archetypeName><position x="10" y="20" z="30"/><parentIndex value="0"/></Item>
<Item type="CEntityDef"><archetypeName>chair</archetypeName><position x="12" y="20" z="30"/><parentIndex value="8"/></Item>
</entities></CMapData>'''
YTYP = b'''<CMapTypes><archetypes><Item type="CBaseArchetypeDef"><name>chair</name><assetName>prop_chair</assetName><assetType>ASSET_TYPE_DRAWABLE</assetType><textureDictionary>shared_textures</textureDictionary><physicsDictionary>chair_bounds</physicsDictionary><lodDist value="100"/></Item></archetypes></CMapTypes>'''
MLO = b'''<CMapTypes><archetypes><Item type="CMloArchetypeDef"><name>test_interior</name><assetName>test_interior</assetName><assetType>ASSET_TYPE_DRAWABLE</assetType>
<entities><Item type="CEntityDef"><archetypeName>chair</archetypeName><position x="1" y="2" z="3"/><parentIndex value="-1"/></Item></entities>
<rooms><Item><name>room_a</name><bbMin x="0" y="0" z="0"/><bbMax x="4" y="4" z="4"/><attachedObjects>0</attachedObjects></Item>
<Item><name>room_b</name><bbMin x="4" y="0" z="0"/><bbMax x="8" y="4" z="4"/></Item></rooms>
<portals><Item><roomFrom value="0"/><roomTo value="1"/><corners><Item>4,0,0,0</Item><Item>4,4,0,0</Item><Item>4,4,4,0</Item><Item>4,0,4,0</Item></corners><attachedObjects>0</attachedObjects></Item></portals>
<entitySets><Item><name>set_a</name><locations>0</locations><entities><Item type="CEntityDef"><archetypeName>chair</archetypeName></Item></entities></Item></entitySets>
</Item></archetypes></CMapTypes>'''
YND = b'''<NodeDictionary><VehicleNodeCount value="2"/><PedNodeCount value="0"/><Nodes>
<Item><AreaID value="1"/><NodeID value="0"/><Position x="1" y="2" z="3"/><Links><Item><ToAreaID value="1"/><ToNodeID value="1"/><LinkLength value="5"/></Item><Item><ToAreaID value="9"/><ToNodeID value="9"/></Item></Links></Item>
<Item><AreaID value="1"/><NodeID value="1"/><Position x="5" y="2" z="3"/></Item>
</Nodes></NodeDictionary>'''
YNV = b'''<NavMesh><ContentFlags>Polygons, Portals</ContentFlags><AreaID value="4"/>
<BBMin x="0" y="0" z="0"/><BBMax x="4" y="4" z="4"/><BBSize x="4" y="4" z="4"/>
<Polygons><Item><Flags>1 2 3</Flags><Vertices>0,0,0
2,0,0
0,2,0</Vertices><Edges>4:0, 4:1
4:0, 5:2
4:0, 16383:16383</Edges><EdgesFlags/></Item><Item><Vertices>2,0,0
2,2,0
0,2,0</Vertices><Edges>4:1, 4:0
4:1, 16383:16383
4:1, 16383:16383</Edges><EdgesFlags/></Item></Polygons><Portals><Item><Type value="1"/><PolyFrom value="0"/><PolyTo value="1"/><PositionFrom x="1" y="0" z="0"/><PositionTo x="2" y="1" z="0"/></Item></Portals><Points><Item><Position x="1" y="1" z="0"/></Item></Points></NavMesh>'''


def test_map_indices_do_not_shift_when_an_entity_has_no_position():
    graph = relationships.analyze(YMAP, ".ymap")
    assert any(edge["source"] == "entity:1" and edge["target"] == "entity:0" and edge["resolution"] == "local" for edge in graph["edges"])
    assert next(node for node in graph["nodes"] if node["id"] == "entity:0").get("position") is None
    assert next(node for node in graph["nodes"] if node["id"] == "entity:1")["position"] == [10, 20, 30]
    assert any(edge["resolution"] == "missing parent index" for edge in graph["edges"])
    assert graph["warnings"]


def test_archetype_dependencies_are_typed_unresolved_search_candidates():
    graph = relationships.analyze(YTYP, ".ytyp")
    assert {node["kind"] for node in graph["nodes"]} == {"archetype", "asset", "texture dictionary", "physics dictionary"}
    assert {edge["resolution"] for edge in graph["edges"]} == {"external: unresolved"}
    assert any(node.get("search") == "prop_chair" for node in graph["nodes"])


def test_mlo_room_portal_and_entity_indices_are_scoped_to_the_archetype():
    graph = relationships.analyze(MLO, ".ytyp")
    portal = next(node for node in graph["nodes"] if node["id"] == "archetype:0:portal:0")
    assert portal["position"] == [4, 2, 2] and len(portal["vertices"]) == 4
    assert any(edge["source"] == portal["id"] and edge["target"] == "archetype:0:room:1" and edge["label"] == "roomTo" for edge in graph["edges"])
    assert any(edge["source"] == "archetype:0:room:0" and edge["target"] == "archetype:0:entity:0" for edge in graph["edges"])
    assert "archetype-local" in graph["scope"]
    assert any("raw values" in warning for warning in graph["warnings"])
    broken = relationships.analyze(MLO.replace(b'roomTo value="1"', b'roomTo value="8"'), ".ytyp")
    assert any(edge["resolution"] == "missing room index" for edge in broken["edges"])


def test_path_links_distinguish_local_external_and_ambiguous_ids():
    graph = relationships.analyze(YND, ".ynd")
    assert [edge["resolution"] for edge in graph["edges"]] == ["local", "external: unresolved"]
    duplicate = YND.replace(b"</Nodes>", b'<Item><AreaID value="1"/><NodeID value="1"/><Position x="8" y="2" z="3"/></Item></Nodes>')
    graph = relationships.analyze(duplicate, ".ynd")
    assert graph["edges"][0]["resolution"] == "ambiguous duplicate ID"


def test_navmesh_edges_use_exact_area_polygon_identity_and_preserve_portals():
    graph = relationships.analyze(YNV, ".ynv")
    assert any(edge["source"] == "poly:0" and edge["target"] == "poly:1" and edge["resolution"] == "local" for edge in graph["edges"])
    assert any(edge["target"] == "external-poly:5:2" for edge in graph["edges"])
    assert not any("16383" in edge["target"] for edge in graph["edges"])
    assert len(next(node for node in graph["nodes"] if node["id"] == "portal:0")["vertices"]) == 2


@pytest.mark.parametrize("data", [b'<!DOCTYPE CMapData [<!ENTITY x "a">]><CMapData>&x;</CMapData>', b'<wrong/>', b'<CMapData><entities><Item><position x="nan"/></Item></entities></CMapData>'])
def test_unsafe_or_unknown_xml_is_not_reported_as_a_valid_empty_graph(data):
    with pytest.raises((ValueError, etree.XMLSyntaxError)):
        relationships.analyze(data, ".ymap")


def test_display_limits_remain_explicit_without_losing_total_counts(monkeypatch):
    monkeypatch.setattr(relationships, "MAX_NODES", 2)
    monkeypatch.setattr(relationships, "MAX_EDGES", 1)
    graph = relationships.analyze(YTYP, ".ytyp")
    assert graph["truncated"] and len(graph["nodes"]) == 2 and graph["node_count"] == 4
    assert len(graph["edges"]) == 1 and graph["edge_count"] == 3


def test_polygon_vertices_are_validated_even_when_the_centroid_is_in_range():
    with pytest.raises(ValueError, match="coordinates"):
        relationships.analyze(b'<NavMesh><Polygons><Item><Vertices>2000000000,0,0\n-2000000000,0,0\n0,0,0</Vertices></Item></Polygons></NavMesh>', ".ynv")


def test_browser_relationship_fixture_matches_the_backend_contract():
    fixture = Path(__file__).resolve().parents[1] / "desktop/src/nativeRelationshipFixture.json"
    assert json.loads(fixture.read_text()) == relationships.analyze(YMAP, ".ymap")


@pytest.mark.parametrize("suffix,xml", [(".ymap", YMAP), (".ytyp", YTYP), (".ynd", YND), (".ynv", YNV)])
def test_native_workspace_exposes_read_only_relationships(tmp_path, suffix, xml):
    context = workspace(tmp_path / "native", suffix)
    path = tmp_path / "native/edit" / ("asset" + suffix + ".xml")
    path.write_bytes(xml)
    first = workspace_desktop.inspect(context)
    assert first["relationships"]["read_only"] and first["relationships"]["nodes"]
    assert workspace_desktop.inspect(context)["state_sha256"] == first["state_sha256"]
    assert path.read_bytes() == xml


@pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST") != "1", reason="Requires actual native converter")
@pytest.mark.parametrize("edition", ["Legacy", "Enhanced"])
@pytest.mark.parametrize("suffix,xml", [(".ymap", YMAP), (".ytyp", YTYP), (".ynd", YND), (".ynv", YNV), (".ytyp", MLO)], ids=["ymap", "ytyp", "ynd", "ynv", "mlo"])
def test_real_converter_relationships_from_source_and_exported_workspace(tmp_path, edition, suffix, xml):
    from allin1_sdk.native_assets import NativeAssetInspector
    from allin1_sdk.paths import project_root
    from allin1_sdk.processes import run_hidden

    inspector = NativeAssetInspector(project_root())
    source_xml = tmp_path / ("fixture" + suffix + ".xml")
    source_xml.write_bytes(xml)
    source = tmp_path / ("fixture" + suffix)
    generated = run_hidden([str(inspector.patcher), "asset-from-xml", str(source_xml), str(source), str(tmp_path),
                            "legacy" if edition == "Legacy" else "gen9"], capture_output=True, text=True, timeout=60)
    assert generated.returncode == 0, generated.stderr or generated.stdout
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    context = {"module": "native", "source": str(source), "edition": edition}
    inspected = workspace_desktop.inspect(context)
    assert inspected["relationships"]["nodes"], inspected["warnings"]
    graph = inspected["relationships"]
    if suffix == ".ynv":
        polygon = next(node for node in graph["nodes"] if node["id"] == "poly:0")
        assert polygon["position"] == pytest.approx([2 / 3, 2 / 3, 0], abs=0.001)
        assert any(edge["source"] == "poly:0" and edge["target"] == "poly:1" and edge["resolution"] == "local" for edge in graph["edges"])
    elif suffix == ".ymap":
        assert next(node for node in graph["nodes"] if node["id"] == "entity:1")["position"] == [10, 20, 30]
    elif suffix == ".ynd":
        assert next(node for node in graph["nodes"] if node["id"] == "node:1")["position"] == [5, 2, 3]
    elif xml == MLO:
        assert next(node for node in graph["nodes"] if node["id"] == "archetype:0:portal:0")["position"] == [4, 2, 2]
        assert any(edge["label"] == "attached object" and edge["resolution"] == "local" for edge in graph["edges"])
    request = {**context, "action": "export", "destination": str(tmp_path / "workspace"), "expected_state_sha256": inspected["state_sha256"]}
    review = workspace_desktop.review(request)
    exported = workspace_desktop.apply({**request, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    assert exported["session"]["relationships"] == inspected["relationships"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
