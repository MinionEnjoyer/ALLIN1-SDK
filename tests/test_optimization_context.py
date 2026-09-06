from copy import deepcopy
import json
import os
from pathlib import Path
import shutil

from lxml import etree
import pytest

from allin1_sdk import optimization_package as optimization, package_validation
from allin1_sdk.workspace_desktop import _inventory,file_hash
from test_artifact_identity import build
from test_optimization_package import request


@pytest.fixture(autouse=True)
def deterministic_identity(monkeypatch):
    monkeypatch.setattr(optimization.artifact_identity,"current",build)


def shared_request(tmp_path):
    payload=request(tmp_path)
    source=Path(payload["source"])
    context=tmp_path/"context"
    context.mkdir()
    shutil.copytree(source/"assets",context/"assets")
    shutil.copyfile(source/"car.ydr.xml",context/"shared.ydr.xml")
    texture=etree.parse(str(source/"paint.ytd.xml"))
    texture.getroot()[0].find("Name").text="parent_texture"
    (context/"shared.ytd.xml").write_bytes(etree.tostring(texture))
    (context/"parents.meta").write_text("<CMapParentTxds><txdRelationships><Item><child>paint</child><parent>shared</parent></Item></txdRelationships></CMapParentTxds>")
    model=etree.parse(str(source/"car.ydr.xml"))
    model.getroot().remove(model.getroot().find("Skeleton"))
    parameter=etree.SubElement(model.find("ShaderGroup/Shaders/Item/Parameters"),"Item",name="ExtraSampler",type="Texture")
    etree.SubElement(parameter,"Name").text="parent_texture"
    (source/"car.ydr.xml").write_bytes(etree.tostring(model))
    payload["comparison"]=str(context)
    payload["settings"]["rig_bindings"]=[{"model":"package:car.ydr.xml","drawable":0,"rig":"comparison:shared.ydr.xml","rig_drawable":0,
        "model_sha256":file_hash(source/"car.ydr.xml"),"rig_sha256":file_hash(context/"shared.ydr.xml")}]
    return payload


def test_same_rig_and_parent_context_is_bound_to_both_reports_export_and_recovery(tmp_path):
    payload=shared_request(tmp_path)
    original=_inventory(Path(payload["source"]))
    context=_inventory(Path(payload["comparison"]))
    value=optimization.inspect(payload)
    for key in ("before_report","after_report"):
        report=value[key]
        assert next(c for c in report["checks"] if c["category"]=="skinning")["status"]=="pass"
        texture_check=next(c for c in report["checks"] if c["category"]=="textures")
        assert texture_check["status"] in {"pass","warning"}
        assert all(f["code"]=="partial_mip_chain" for f in texture_check["findings"])
        assert report["shared_rigs"][0]["rig_sha256"]==payload["settings"]["rig_bindings"][0]["rig_sha256"]
        assert any(row["dictionary"]=="comparison:shared.ytd.xml" for row in report["texture_resolutions"])
    assert value["validation_context"]["shared_rig_count"]==1
    export={**payload,"action":"export","destination":str(tmp_path/"export"),"expected_state_sha256":value["state_sha256"]}
    optimization.review(export)
    receipt=optimization.apply(export)
    written=json.loads(Path(receipt["receipt"]).read_bytes())
    assert written["before_report"]["report_sha256"]==value["before_report"]["report_sha256"]
    assert not (tmp_path/"export/package/shared.ydr.xml").exists()
    assert _inventory(Path(payload["comparison"]))==context
    assert _inventory(tmp_path/"export/originals")==original
    recovery=optimization.inspect({"workspace":str(tmp_path/"export")})
    with pytest.raises(ValueError,match="outside the source"):
        optimization.apply({"action":"recover","workspace":str(tmp_path/"export"),"destination":str(tmp_path/"export/nested"),"expected_state_sha256":recovery["state_sha256"]})
    optimization.apply({"action":"recover","workspace":str(tmp_path/"export"),"destination":str(tmp_path/"recovered"),"expected_state_sha256":recovery["state_sha256"]})
    assert _inventory(tmp_path/"recovered")==original


def test_context_change_invalidates_a_review_without_creating_an_output(tmp_path):
    payload=shared_request(tmp_path)
    value=optimization.inspect(payload)
    export={**payload,"action":"export","destination":str(tmp_path/"export"),"expected_state_sha256":value["state_sha256"]}
    optimization.review(export)
    (Path(payload["comparison"])/"additional.txt").write_text("context changed")
    with pytest.raises(ValueError,match="changed"):
        optimization.apply(export)
    assert not (tmp_path/"export").exists()


def test_context_cannot_change_between_the_two_reports(tmp_path,monkeypatch):
    payload=shared_request(tmp_path)
    real=package_validation.inspect
    calls=0
    def racing(*args,**kwargs):
        nonlocal calls
        value=real(*args,**kwargs)
        calls+=1
        if calls==1:
            (Path(payload["comparison"])/"late.txt").write_text("changed between reports")
        return value
    monkeypatch.setattr(package_validation,"inspect",racing)
    with pytest.raises(ValueError,match="Comparison context changed"):
        optimization.inspect(payload)


def test_final_publication_rechecks_context_after_copying(tmp_path,monkeypatch):
    payload=shared_request(tmp_path)
    value=optimization.inspect(payload)
    real=optimization._copy
    def racing(source,destination,inventory):
        real(source,destination,inventory)
        if destination.name=="package":
            (Path(payload["comparison"])/"late.txt").write_text("changed while exporting")
    monkeypatch.setattr(optimization,"_copy",racing)
    with pytest.raises(ValueError,match="comparison context changed"):
        optimization.apply({**payload,"action":"export","destination":str(tmp_path/"export"),"expected_state_sha256":value["state_sha256"]})
    assert not (tmp_path/"export").exists()


def test_stale_rig_hashes_and_outputs_inside_comparison_are_rejected(tmp_path):
    payload=shared_request(tmp_path)
    stale=deepcopy(payload)
    stale["settings"]["rig_bindings"][0]["rig_sha256"]="0"*64
    with pytest.raises(ValueError,match="changed"):
        optimization.inspect(stale)
    value=optimization.inspect(payload)
    with pytest.raises(ValueError,match="outside the comparison"):
        optimization.review({**payload,"action":"export","destination":str(Path(payload["comparison"])/"output"),"expected_state_sha256":value["state_sha256"]})
    with pytest.raises(ValueError,match="outside the comparison"):
        optimization.apply({**payload,"action":"export","destination":str(Path(payload["comparison"])/"output"),"expected_state_sha256":value["state_sha256"]})


def test_export_cannot_publish_a_receipt_its_recovery_reader_would_reject(tmp_path,monkeypatch):
    payload=shared_request(tmp_path)
    value=optimization.inspect(payload)
    monkeypatch.setattr(optimization,"MAX_RECEIPT_BYTES",1024)
    export={**payload,"action":"export","destination":str(tmp_path/"export"),"expected_state_sha256":value["state_sha256"]}
    for operation in (optimization.review,optimization.apply):
        with pytest.raises(ValueError,match="recovery reader"):
            operation(export)
    assert not (tmp_path/"export").exists()


@pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST")!="1",reason="explicit native helper gate")
@pytest.mark.parametrize("edition",["Legacy","Enhanced"])
def test_single_rpf_with_exact_model_rig_selection_exports_and_recovers(tmp_path,edition):
    from test_package_intake import native_archive
    archive,game=native_archive(tmp_path,edition,size=16)
    original=archive.read_bytes()
    report=package_validation.inspect(archive,edition=edition,gta_path=str(game))
    model=report["drawable_candidates"][0]
    binding={"model":model["source"],"drawable":model["drawable"],"rig":model["source"],"rig_drawable":model["drawable"],"model_sha256":model["sha256"],"rig_sha256":model["sha256"]}
    payload={"source":str(archive),"edition":edition,"gta_path":str(game),"settings":{"rig_bindings":[binding],"textures":[
        {"dictionary":"dlc.rpf.source/vehicles.rpf.source/paint.ytd","texture":"fixture_diffuse","format":"DXT5","mips":5,"role":"color"}]}}
    value=optimization.inspect(payload)
    assert value["source"]==str(archive) and value["source_kind"]=="rpf_archive"
    assert value["before_report"]["shared_rigs"]==value["after_report"]["shared_rigs"]
    assert value["before_inventory"]=={"dlc.rpf":file_hash(archive)}
    export={**payload,"action":"export","destination":str(tmp_path/"export"),"expected_state_sha256":value["state_sha256"]}
    optimization.review(export)
    optimization.apply(export)
    assert (tmp_path/"export/originals/dlc.rpf").read_bytes()==original
    assert archive.read_bytes()==original
    recovery=optimization.inspect({"workspace":str(tmp_path/"export")})
    optimization.apply({"action":"recover","workspace":str(tmp_path/"export"),"destination":str(tmp_path/"recovered"),"expected_state_sha256":recovery["state_sha256"]})
    assert (tmp_path/"recovered/dlc.rpf").read_bytes()==original
