import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from lxml import etree

from allin1_sdk import rel_relationships, native_relationships, workspace_desktop
from allin1_sdk.native_assets import NativeAssetInspector
from allin1_sdk.paths import project_root
from allin1_sdk.processes import run_hidden

REL = b'''<Dat54><Version value="1"/><ContainerPaths><Item>audio/demo.awc</Item></ContainerPaths><Items>
<Item type="LoopingSound"><Name>demo_a</Name><Header><Flags value="32768"/><Category>demo_b</Category></Header><ChildSound>demo_b</ChildSound></Item>
<Item type="LoopingSound"><Name>demo_b</Name><Header><Flags value="0"/></Header><ChildSound>demo_external</ChildSound></Item>
</Items></Dat54>'''


def fake_helper(monkeypatch, tmp_path, result):
    helper = tmp_path / "tools/RpfPatcher/RpfPatcher.exe"
    helper.parent.mkdir(parents=True)
    helper.write_bytes(b"not-executed")
    monkeypatch.setattr(rel_relationships, "project_root", lambda: tmp_path)
    def run(command, **kwargs):
        assert command[1] == "rel-relationships" and Path(command[2]).read_bytes() == REL
        assert kwargs["timeout"] == 30
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(rel_relationships, "run_hidden", run)


def test_rel_graph_requires_a_current_native_helper(monkeypatch, tmp_path):
    monkeypatch.setattr(rel_relationships, "project_root", lambda: tmp_path)
    with pytest.raises(ValueError, match="current RpfPatcher"):
        rel_relationships.analyze(REL)


@pytest.mark.parametrize("result", [
    SimpleNamespace(returncode=1, stdout="", stderr="Unknown command"),
    SimpleNamespace(returncode=0, stdout='{"kind":"wrong"}', stderr=""),
    SimpleNamespace(returncode=0, stdout='{"schema_version":1,"schema_version":2}', stderr=""),
    SimpleNamespace(returncode=0, stdout="not JSON", stderr=""),
    subprocess.TimeoutExpired("helper", 30),
])
def test_rel_decoder_failures_are_not_reported_as_an_empty_valid_graph(monkeypatch, tmp_path, result):
    fake_helper(monkeypatch, tmp_path, result)
    with pytest.raises(ValueError):
        rel_relationships.analyze(REL)


def test_rel_helper_counts_and_schema_are_bound(monkeypatch, tmp_path):
    graph = {"schema_version": 1, "format": ".rel", "read_only": True, "nodes": [], "edges": [], "warnings": [], "scope": "typed", "node_count": 0, "edge_count": 0}
    result = SimpleNamespace(returncode=0, stdout=json.dumps(graph), stderr="")
    fake_helper(monkeypatch, tmp_path, result)
    assert native_relationships.analyze(REL, ".rel") == graph
    graph["node_count"] = -1
    result.stdout = json.dumps(graph)
    with pytest.raises(ValueError, match="counts"):
        rel_relationships.analyze(REL)


native = pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST") != "1", reason="Requires native REL decoder")


@native
def test_typed_rel_links_and_family_separation():
    graph = rel_relationships.analyze(REL)
    fixture = Path(__file__).resolve().parents[1] / "desktop/src/nativeRelRelationshipFixture.json"
    assert json.loads(fixture.read_text()) == graph
    assert any(edge["label"] == "sound" and edge["resolution"] == "local" for edge in graph["edges"])
    assert any(edge["label"] == "category" and edge["resolution"] == "external: unresolved" for edge in graph["edges"])
    assert any(node.get("search") == "demo.awc" for node in graph["nodes"])
    assert "normalized inventory" in graph["scope"]


@native
def test_duplicate_rel_hashes_remain_ambiguous():
    graph = rel_relationships.analyze(REL.replace(b"demo_b", b"demo_a"))
    assert any(edge["resolution"] == "ambiguous duplicate hash" for edge in graph["edges"])


@native
def test_rel_display_limits_keep_total_counts():
    items = b"".join(b'<Item type="LoopingSound"><Name>sound_' + str(i).encode() + b'</Name><Header><Flags value="32768"/><Category>sound_0</Category></Header><ChildSound>sound_0</ChildSound></Item>' for i in range(1100))
    graph = rel_relationships.analyze(b'<Dat54><Version value="1"/><Items>' + items + b'</Items></Dat54>')
    assert graph["truncated"] and len(graph["nodes"]) == 1000 and graph["node_count"] == 1101
    assert len(graph["edges"]) == 1800 and graph["edge_count"] == 2200 and graph["warnings"]


@native
@pytest.mark.parametrize("xml,label", [
    (b'<Dat22><Version value="1"/><Items><Item type="Category"><Name>parent</Name><SubCategories><Item>child</Item></SubCategories><LPFDistanceCurve>curve_external</LPFDistanceCurve></Item><Item type="Category"><Name>child</Name></Item></Items></Dat22>', "category"),
    (b'<Dat151><Version value="1"/><Items><Item type="StaticEmitterList"><Name>parent</Name><Emitters><Item>child</Item></Emitters></Item><Item type="StaticEmitterList"><Name>child</Name></Item></Items></Dat151>', "game"),
])
def test_rel_category_and_game_reference_families(xml, label):
    graph = rel_relationships.analyze(xml)
    assert any(edge["label"] == label and edge["resolution"] == "local" for edge in graph["edges"])
    if label == "category":
        assert any(edge["label"] == "curve" and edge["resolution"] == "external: unresolved" for edge in graph["edges"])


@native
def test_lossy_rel_normalization_is_not_presented_as_a_complete_graph():
    children = b"".join(b"<Item>child_" + str(i).encode() + b"</Item>" for i in range(300))
    xml = b'<Dat22><Version value="1"/><Items><Item type="Category"><Name>parent</Name><SubCategories>' + children + b'</SubCategories></Item></Items></Dat22>'
    # The native field is a byte count: ReadXml casts 300 to 44. Detect that loss.
    with pytest.raises(ValueError, match="changed during normalization"):
        rel_relationships.analyze(xml)


@native
@pytest.mark.parametrize("edition", ["Legacy", "Enhanced"])
def test_rel_source_workspace_edit_build_and_relationship_refresh(tmp_path, edition):
    inspector = NativeAssetInspector(project_root())
    xml = tmp_path / "demo.rel.xml"
    xml.write_bytes(REL)
    source = tmp_path / "demo.rel"
    result = run_hidden([str(inspector.patcher), "asset-from-xml", str(xml), str(source), str(tmp_path), "legacy" if edition == "Legacy" else "gen9"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    context = {"module": "native", "source": str(source), "edition": edition}
    def apply(context, action, **fields):
        session = workspace_desktop.inspect(context)
        request = {**context, "action": action, "expected_state_sha256": session["state_sha256"], **fields}
        review = workspace_desktop.review(request)
        return workspace_desktop.apply({**request, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    inspected = workspace_desktop.inspect(context)
    assert inspected["relationships"]["format"] == ".rel"
    exported = apply(context, "export", destination=str(tmp_path / "workspace"))
    workspace = {"module": "native", "workspace": exported["session"]["workspace"]}
    graph_before = exported["session"]["relationships"]
    assert graph_before == inspected["relationships"]
    document = etree.fromstring("".join(exported["session"]["xml_chunks"]).encode())
    for child in document.findall("Items/Item/ChildSound"):
        child.text = "hash_00000000"
    changed = apply(workspace, "save_xml", document={"language": "xml", "chunks": [etree.tostring(document).decode()]})
    assert not any(edge["label"] == "sound" for edge in changed["session"]["relationships"]["edges"])
    built = apply(workspace, "build", destination=str(tmp_path / "rebuilt.rel"))
    assert built["validation"]["reparsed"] is True
    inspected_build = workspace_desktop.inspect({"module": "native", "source": str(tmp_path / "rebuilt.rel"), "edition": edition})
    assert inspected_build["relationships"] == changed["session"]["relationships"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
