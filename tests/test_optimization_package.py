import json
import os

from PIL import Image
import pytest

from allin1_sdk import optimization_package as optimization, workspace_desktop as desktop
from test_texture_validation import package_fixture
from test_artifact_identity import build


@pytest.fixture(autouse=True)
def deterministic_build_identity(monkeypatch):
    # Unit tests isolate package transaction behavior. The React integration
    # captures the real executing SDK and native helper identity.
    monkeypatch.setattr(optimization.artifact_identity,"current",build)


def request(tmp_path):
    root = package_fixture(tmp_path)
    # A measurable opaque color input, not a 1x1 compression expansion.
    Image.new("RGBA", (16,16), (42,85,119,255)).save(root/"assets/diffuse.dds", format="DDS")
    xml = root/"paint.ytd.xml"
    xml.write_text(xml.read_text().replace('Width value="1"', 'Width value="16"').replace('Height value="1"', 'Height value="16"'))
    return {"source":str(root), "settings":{"textures":[{"dictionary":"paint.ytd.xml","texture":"fixture_diffuse","format":"DXT5","mips":5,"role":"color"}]}}


def test_preview_export_and_exact_recovery_without_source_mutation(tmp_path):
    payload = request(tmp_path)
    before = desktop._inventory(tmp_path/"package")
    result = optimization.inspect(payload)
    assert result["storage_delta_bytes"] < 0
    assert result["before_report"]["source_sha256"] == desktop.digest(before)
    assert result["changes"][0]["after"]["mip_levels"] == 5
    export = {**payload,"action":"export","destination":str(tmp_path/"export"),"expected_state_sha256":result["state_sha256"]}
    optimization.review(export)
    receipt = optimization.apply(export)
    exported = json.loads((tmp_path/"export/optimization.json").read_text())
    assert desktop._inventory(tmp_path/"export/originals") == before
    assert desktop._inventory(tmp_path/"package") == before
    actual = desktop._inventory(tmp_path/"export/package")
    actual.pop("sdk-artifact.json")
    assert actual == result["after_inventory"]
    from allin1_sdk.artifact_contract import validate_manifest
    artifact = validate_manifest(json.loads((tmp_path/"export/package/sdk-artifact.json").read_text()))
    assert artifact["outputs"] == result["after_inventory"]
    assert exported["after_report"]["runtime_status"] == "not_tested"
    recover = {"action":"recover","workspace":str(tmp_path/"export"),"destination":str(tmp_path/"recovered"),"expected_state_sha256":receipt["recovery_state_sha256"]}
    optimization.review(recover)
    optimization.apply(recover)
    assert desktop._inventory(tmp_path/"recovered") == before


def test_stale_sources_and_modified_recovery_originals_are_rejected(tmp_path):
    payload = request(tmp_path)
    result = optimization.inspect(payload)
    export = {**payload,"action":"export","destination":str(tmp_path/"export"),"expected_state_sha256":result["state_sha256"]}
    optimization.apply(export)
    (tmp_path/"package/new.txt").write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        optimization.review({**export,"destination":str(tmp_path/"other")})
    (tmp_path/"export/originals/car.ydr.xml").write_text("tampered")
    with pytest.raises(ValueError, match="originals were modified"):
        optimization.recovery({"workspace":str(tmp_path/"export")})


def test_pixel_navigation_keeps_exact_candidate_identity_and_rejects_stale_input(tmp_path):
    payload=request(tmp_path)
    baseline=optimization.inspect(payload)
    pixel={**payload,"expected_state_sha256":baseline["state_sha256"],"preview_region":{
        "dictionary":"paint.ytd.xml","texture":"fixture_diffuse","mip":0,"x":4,"y":5,"channel":"rgb"}}
    result=optimization.inspect(pixel)
    assert result["state_sha256"]==baseline["state_sha256"]
    assert result["artifact_manifest"]==baseline["artifact_manifest"]
    assert result["changes"][0]["preview_region"]["width"]==12
    with pytest.raises(ValueError,match="changed"):
        optimization.inspect({**pixel,"expected_state_sha256":"0"*64})
    with pytest.raises(ValueError,match="queued"):
        optimization.inspect({**pixel,"preview_region":{**pixel["preview_region"],"texture":"unselected"}})


def test_shared_payload_and_unreviewed_output_are_not_silently_allowed(tmp_path):
    payload = request(tmp_path)
    xml = tmp_path/"package/paint.ytd.xml"
    (tmp_path/"package/alias.ytd.xml").write_bytes(xml.read_bytes())
    with pytest.raises(ValueError, match="multiple dictionary owners"):
        optimization.inspect(payload)


def test_no_selection_cannot_export_and_unknown_role_cannot_encode(tmp_path):
    payload = request(tmp_path)
    result = optimization.inspect({**payload,"settings":{}})
    assert len(result["choices"]) == 1 and not result["changes"]
    with pytest.raises(ValueError, match="at least one"):
        optimization.review({**payload,"settings":{},"action":"export","destination":str(tmp_path/"empty"),"expected_state_sha256":result["state_sha256"]})
    payload["settings"]["textures"][0]["role"] = "unknown"
    with pytest.raises(ValueError, match="material role"):
        optimization.inspect(payload)


def test_broker_requires_confirmation_and_rejects_changed_plan_and_destination(tmp_path):
    payload = {"module":"optimization", **request(tmp_path)}
    session = desktop.inspect(payload)
    export = {**payload,"action":"export","destination":str(tmp_path/"export"),"expected_state_sha256":session["state_sha256"]}
    reviewed = desktop.review(export)
    with pytest.raises(ValueError, match="confirmation"):
        desktop.apply({**export,"review_sha256":reviewed["review_sha256"]})
    altered = {**export,"settings":{"textures":[{**payload["settings"]["textures"][0],"format":"RGBA8"}]}}
    with pytest.raises(ValueError, match="changed"):
        desktop.apply({**altered,"review_sha256":reviewed["review_sha256"],"authoring_confirmed":True})
    (tmp_path/"export").mkdir()
    (tmp_path/"export/keep.txt").write_text("existing")
    with pytest.raises(ValueError, match="new destination"):
        desktop.apply({**export,"review_sha256":reviewed["review_sha256"],"authoring_confirmed":True})
    assert (tmp_path/"export/keep.txt").read_text() == "existing"


@pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST") != "1", reason="explicit native helper gate")
@pytest.mark.parametrize("edition", ["Legacy", "Enhanced"])
def test_native_dictionary_candidate_rebuilds_and_reparses_for_both_editions(tmp_path, edition):
    from allin1_sdk.paths import project_root
    from allin1_sdk.processes import run_hidden
    payload = request(tmp_path)
    root = tmp_path/"package"
    xml, output = root/"paint.ytd.xml", root/"paint.ytd"
    result = run_hidden([str(project_root()/"tools/RpfPatcher/RpfPatcher.exe"),"asset-from-xml",str(xml),str(output),str(root/"assets"),"legacy" if edition=="Legacy" else "gen9"],capture_output=True,text=True,timeout=60)
    assert result.returncode == 0, result.stderr or result.stdout
    xml.unlink()
    payload["edition"] = edition
    payload["settings"]["textures"][0]["dictionary"] = "paint.ytd"
    before = desktop._inventory(root)
    evidence = optimization.inspect(payload)
    assert evidence["storage_delta_bytes"] < 0
    assert evidence["after_inventory"]["paint.ytd"] != before["paint.ytd"]
    assert evidence["after_inventory"]["car.ydr.xml"] == before["car.ydr.xml"]
    assert evidence["after_report"]["texture_costs"][0]["mip_levels"] == 5
    assert desktop._inventory(root) == before


@pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST") != "1", reason="explicit native helper gate")
@pytest.mark.parametrize("edition", ["Legacy", "Enhanced"])
def test_packed_native_candidate_rebuilds_nested_rpf_and_recovers_original_container(tmp_path,edition):
    from test_package_intake import native_archive
    import shutil
    inputs=tmp_path/"inputs";inputs.mkdir()
    archive,game=native_archive(inputs,edition,size=16)
    source=tmp_path/"packed";source.mkdir();shutil.copyfile(archive,source/"dlc.rpf")
    original=(source/"dlc.rpf").read_bytes()
    payload={"source":str(source),"edition":edition,"gta_path":str(game),"settings":{"textures":[{"dictionary":"dlc.rpf.source/vehicles.rpf.source/paint.ytd","texture":"fixture_diffuse","format":"DXT5","mips":5,"role":"color"}]}}
    result=optimization.inspect(payload)
    assert result["storage_delta_bytes"]<0
    assert len(result["archive_rebuilds"])==1
    assert result["archive_rebuilds"][0]["container"]=="dlc.rpf"
    assert result["before_inventory"]["dlc.rpf"]!=result["after_inventory"]["dlc.rpf"]
    before_members={row["entry_id"]:row["content_sha256"] for row in result["before_report"]["archive_provenance"]["package"]["members"]}
    after_members={row["entry_id"]:row["content_sha256"] for row in result["after_report"]["archive_provenance"]["package"]["members"]}
    assert before_members["vehicles.rpf::car.ydr"]==after_members["vehicles.rpf::car.ydr"]
    assert before_members["vehicles.rpf::vehicles.meta"]==after_members["vehicles.rpf::vehicles.meta"]
    export={**payload,"action":"export","destination":str(tmp_path/"export"),"expected_state_sha256":result["state_sha256"]}
    optimization.review(export)
    built=optimization.apply(export)
    assert (source/"dlc.rpf").read_bytes()==original
    assert (tmp_path/"export/originals/dlc.rpf").read_bytes()==original
    recovery={"action":"recover","workspace":str(tmp_path/"export"),"destination":str(tmp_path/"recovered"),"expected_state_sha256":built["recovery_state_sha256"]}
    optimization.review(recovery);optimization.apply(recovery)
    assert (tmp_path/"recovered/dlc.rpf").read_bytes()==original
