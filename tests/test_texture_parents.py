import hashlib
from pathlib import Path
import shutil

from lxml import etree
import pytest

from allin1_sdk import package_validation
from allin1_sdk.texture_dependencies import TextureDependencies
from test_texture_validation import package_fixture, textures


def parent_package(tmp_path):
    source=package_fixture(tmp_path)
    context=tmp_path/"shared-context"
    context.mkdir()
    shutil.copytree(source/"assets",context/"assets")
    shutil.copyfile(source/"paint.ytd.xml",context/"shared.ytd.xml")
    (source/"paint.ytd.xml").write_text("<TextureDictionary/>")
    metadata=source/"parents.meta"
    metadata.write_text("<CMapParentTxds><txdRelationships><Item><parent>shared</parent><child>paint</child></Item></txdRelationships></CMapParentTxds>")
    return source,context,metadata


def test_explicit_comparison_parent_resolves_verified_bytes_with_exact_metadata_provenance(tmp_path):
    source,context,metadata=parent_package(tmp_path)
    before=metadata.read_bytes()
    report=package_validation.inspect(source,comparison=context)
    assert textures(report)["status"]=="pass",textures(report)
    resolution=report["texture_resolutions"][0]
    assert resolution["dictionary"]=="comparison:shared.ytd.xml"
    assert resolution["parent_chain"]==[{"child":"paint","parent":"shared"}]
    assert resolution["dictionary_chain"]==[{"dictionary":"paint","source":"package:paint.ytd.xml"},{"dictionary":"shared","source":"comparison:shared.ytd.xml"}]
    assert resolution["payload_sha256"]==report["texture_costs"][0]["sha256"]
    assert report["texture_parent_relationships"][0]["source_sha256"]==hashlib.sha256(before).hexdigest()
    assert metadata.read_bytes()==before
    assert report["runtime_status"]=="not_tested"


def test_parent_resolution_requires_the_explicit_context_not_local_filename_guessing(tmp_path):
    source,_,__=parent_package(tmp_path)
    report=package_validation.inspect(source)
    assert not report["texture_resolutions"]
    assert any(f["code"]=="dictionary_context_unresolved" for f in textures(report)["findings"])


def test_exported_ymt_relationships_are_read_but_undecoded_native_context_is_explicit(tmp_path):
    source,context,metadata=parent_package(tmp_path)
    renamed=metadata.with_suffix(".ymt.xml")
    metadata.rename(renamed)
    report=package_validation.inspect(source,comparison=context)
    assert report["texture_resolutions"][0]["dictionary"]=="comparison:shared.ytd.xml"
    assert report["texture_parent_relationships"][0]["source"]=="package:parents.ymt.xml"
    (source/"unknown.ymt").write_bytes(b"RSC7-undecoded-test")
    report=package_validation.inspect(source,comparison=context)
    assert any(f["code"]=="native_texture_relationships_unparsed" for f in textures(report)["findings"])
    (source/"unknown.ymt").rename(context/"unknown.ymt")
    report=package_validation.inspect(source,comparison=context)
    assert any(f["code"]=="native_texture_relationships_unparsed" and f["location"]=="comparison:unknown.ymt" for f in textures(report)["findings"])


def test_dictionary_and_payload_hash_collisions_block_name_only_resolution(tmp_path,monkeypatch):
    from allin1_sdk import texture_dependencies
    monkeypatch.setattr(texture_dependencies,"joaat",lambda _:123)
    source,context,_=parent_package(tmp_path)
    report=package_validation.inspect(source,comparison=context)
    assert not report["texture_resolutions"]
    assert any(f["code"]=="dictionary_hash_ambiguous" for f in textures(report)["findings"])
    root=etree.parse(str(context/"shared.ytd.xml"))
    extra=etree.fromstring(etree.tostring(root.getroot()[0]))
    extra.find("Name").text="different_name"
    root.getroot().append(extra)
    events=[];dependencies=TextureDependencies(lambda *args:events.append(args))
    dependencies.register("shared",root.getroot(),context/"assets","shared.ytd.xml")
    assert all(value is None for value in dependencies.dictionaries["shared"][0][1].values())
    assert any(row[2]=="texture_hash_ambiguous" for row in events)


@pytest.mark.parametrize("problem",["cycle","conflicting_parent","duplicate_dictionary","bad_payload","missing_child"])
def test_ambiguous_or_invalid_parent_paths_never_resolve_as_pass(tmp_path,problem):
    source,context,metadata=parent_package(tmp_path)
    if problem=="cycle":
        metadata.write_text("<CMapParentTxds><txdRelationships><Item><child>paint</child><parent>paint</parent></Item></txdRelationships></CMapParentTxds>")
    elif problem=="conflicting_parent":
        (context/"other.meta").write_text("<CMapParentTxds><txdRelationships><Item><child>paint</child><parent>other</parent></Item></txdRelationships></CMapParentTxds>")
    elif problem=="duplicate_dictionary":
        shutil.copyfile(context/"shared.ytd.xml",source/"shared.ytd.xml")
    elif problem=="bad_payload":
        (context/"assets/diffuse.dds").write_bytes(b"broken")
    else:
        (source/"paint.ytd.xml").unlink()
    report=package_validation.inspect(source,comparison=context)
    assert not report["texture_resolutions"]
    assert textures(report)["status"]!="pass"


def test_ped_multi_relationships_are_explicit_and_invalid_document_is_atomic():
    dependencies=TextureDependencies(lambda *args:None)
    data=b"<CPedModelInfo__InitDataList><multiTxdRelationships><Item><parent>shared</parent><children><Item>a</Item><Item>b</Item></children></Item></multiTxdRelationships></CPedModelInfo__InitDataList>"
    dependencies.metadata(data,"peds.meta")
    assert set(dependencies.parents)=={"a","b"}
    assert dependencies.parent_relationships[0]["source_sha256"]==hashlib.sha256(data).hexdigest()
    malformed=b"<CMapParentTxds><txdRelationships><Item><child>c</child><parent>shared</parent></Item><Item><parent>bad</parent></Item></txdRelationships></CMapParentTxds>"
    with pytest.raises(ValueError):
        dependencies.metadata(malformed,"bad.meta")
    assert set(dependencies.parents)=={"a","b"}


def test_parent_depth_limit_and_graph_cycles_are_bounded():
    findings=[]
    dependencies=TextureDependencies(lambda *args:findings.append(args))
    root=etree.Element("CMapParentTxds");items=etree.SubElement(root,"txdRelationships")
    for i in range(33):
        item=etree.SubElement(items,"Item")
        etree.SubElement(item,"child").text=str(i)
        etree.SubElement(item,"parent").text=str(i+1)
        dependencies.dictionaries[str(i)].append((f"{i}.ytd",{}))
    dependencies.metadata(etree.tostring(root),"deep.meta")
    assert dependencies.resolve_external("0","texture","test") is None
    assert findings[-1][2]=="texture_parent_depth"
    dependencies.metadata(b"<CMapParentTxds><txdRelationships><Item><child>33</child><parent>0</parent></Item></txdRelationships></CMapParentTxds>","cycle.meta")
    dependencies.validate_parents()
    assert any(row[2]=="texture_parent_cycle" and row[1]=="fail" for row in findings)
