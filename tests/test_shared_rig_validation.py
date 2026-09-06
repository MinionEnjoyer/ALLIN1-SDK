from pathlib import Path
import shutil

from lxml import etree
import pytest

from allin1_sdk import package_validation,asset_validation,animation_model
from allin1_sdk.workspace_desktop import file_hash,_inventory
from test_texture_validation import package_fixture
from test_package_validation import category
from test_attachment_validation import WEAPON,COMPONENT


def fixture(tmp_path):
    root=package_fixture(tmp_path);context=tmp_path/"context";context.mkdir()
    file=root/"car.ydr.xml";shutil.copyfile(file,context/"shared.ydr.xml")
    model=etree.fromstring(file.read_bytes());model.remove(model.find("Skeleton"));file.write_bytes(etree.tostring(model))
    binding={"model":"package:car.ydr.xml","drawable":0,"rig":"comparison:shared.ydr.xml","rig_drawable":0,
             "model_sha256":file_hash(file),"rig_sha256":file_hash(context/"shared.ydr.xml")}
    return root,context,binding


def test_rig_is_never_selected_from_a_matching_name_or_available_candidate(tmp_path):
    root,context,binding=fixture(tmp_path)
    original=_inventory(root);other=_inventory(context)
    report=package_validation.inspect(str(root),comparison=str(context))
    assert category(report,"skeleton")["status"]=="not_checked"
    assert category(report,"skinning")["status"]=="not_checked"
    assert len(report["drawable_candidates"])==2 and not report["shared_rigs"]
    selected=package_validation.inspect(str(root),comparison=str(context),rig_bindings=[binding])
    assert category(selected,"skinning")["status"]=="pass"
    assert category(selected,"skeleton")["status"]=="warning"
    assert selected["shared_rigs"][0]["rig_sha256"]==binding["rig_sha256"]
    assert selected["report_sha256"]!=report["report_sha256"]
    assert selected["source_sha256"]==report["source_sha256"]
    assert selected["runtime_status"]=="not_tested"
    assert _inventory(root)==original and _inventory(context)==other


@pytest.mark.parametrize("field,value",[("drawable",3),("drawable",True),("rig_drawable",-1),("rig_sha256","a"*64),("model_sha256","b"*64),("rig","comparison:missing.ydr.xml")])
def test_stale_or_ambiguous_shared_binding_rejected(tmp_path,field,value):
    root,context,binding=fixture(tmp_path);binding[field]=value
    with pytest.raises(ValueError): package_validation.inspect(str(root),comparison=str(context),rig_bindings=[binding])


def test_existing_skeleton_conflict_is_a_failure_not_replaced_silently(tmp_path):
    root,context,binding=fixture(tmp_path)
    original=(context/"shared.ydr.xml").read_bytes()
    (root/"car.ydr.xml").write_bytes(original)
    binding["model_sha256"]=file_hash(root/"car.ydr.xml")
    rig=etree.fromstring(original);rig.find("Skeleton/Bones/Item/Tag").set("value","1234")
    (context/"shared.ydr.xml").write_bytes(etree.tostring(rig));binding["rig_sha256"]=file_hash(context/"shared.ydr.xml")
    report=package_validation.inspect(str(root),comparison=str(context),rig_bindings=[binding])
    assert category(report,"skeleton")["status"]=="fail"
    assert category(report,"skinning")["status"]=="fail"
    assert (root/"car.ydr.xml").read_bytes()==original


def test_explicit_shared_rig_supplies_attachment_anchor_and_comparison_child(tmp_path):
    root,context,binding=fixture(tmp_path)
    (root/"weapons.meta").write_bytes(WEAPON.replace(b"<Model>body</Model>",b"<Model>car</Model>"))
    (context/"components.meta").write_bytes(COMPONENT)
    shutil.copyfile(context/"shared.ydr.xml",context/"clip.ydr.xml")
    baseline=package_validation.inspect(str(root),comparison=str(context))
    assert not baseline["attachment_bindings"]
    report=package_validation.inspect(str(root),comparison=str(context),rig_bindings=[binding])
    assert len(report["attachment_bindings"])==1
    anchor=report["attachment_bindings"][0]
    assert anchor["bone_tag"]==42 and anchor["skeleton_matrix"][2][3]==1
    assert anchor["child_source"]=="comparison:clip.ydr.xml"
    assert report["runtime_status"]=="not_tested"


def test_model_report_binds_exact_external_skeleton_xml_without_rewriting_source(tmp_path):
    root,context,_=fixture(tmp_path)
    data=(root/"car.ydr.xml").read_bytes();rig=animation_model._drawables((context/"shared.ydr.xml").read_bytes())[0]
    result=asset_validation.analyze(data,rig_owners={0:rig})
    assert category(result,"skinning")["status"]=="pass" and len(result["shared_rigs"])==1
    assert result["source_sha256"]==file_hash(root/"car.ydr.xml")
