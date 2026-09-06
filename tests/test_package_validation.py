import json
from pathlib import Path

import pytest

from allin1_sdk import metadata_validation as metadata, package_validation as package, workspace_desktop as desktop
from allin1_sdk.desktop_protocol import _bounded

FIXTURE = Path(__file__).parent / "fixtures/animation_skin.ydr.xml"


def model_meta(name="car", txd="paint"):
    return f"<CVehicleModelInfo__InitDataList><InitDatas><Item><modelName>{name}</modelName><txdName>{txd}</txdName></Item></InitDatas></CVehicleModelInfo__InitDataList>".encode()


def category(report, name):
    return next(c for c in report["checks"] if c["category"] == name)


def test_definitions_do_not_confuse_model_references_with_model_definitions():
    records, supported = metadata.definitions(model_meta(), "vehicles.meta")
    assert supported and len(records)==1 and records[0]["namespace"]=="model"
    refs, supported = metadata.definitions(b"<CVehicleModelInfoVariation><variationData><Item><modelName>car</modelName></Item></variationData></CVehicleModelInfoVariation>", "carvariations.meta")
    assert refs==[] and not supported


def test_weapon_namespaces_exclude_component_and_ammo_references():
    source=b'<CWeaponInfoBlob><Infos><Item><Infos><Item type="CWeaponInfo"><Name>WEAPON_TEST</Name><AmmoInfo ref="AMMO_TEST"/><AttachPoints><Item><Components><Item><Name>COMPONENT_TEST</Name></Item></Components></Item></AttachPoints></Item><Item type="CAmmoInfo"><Name>AMMO_TEST</Name><AmmoMax value="5"/></Item></Infos></Item></Infos></CWeaponInfoBlob>'
    records, supported=metadata.definitions(source,"weapons.meta")
    assert supported
    assert [(r["namespace"],r["name"]) for r in records]==[("weapon","WEAPON_TEST"),("ammo","AMMO_TEST")]


def test_duplicate_conflict_and_override_are_distinct():
    a,_=metadata.definitions(model_meta(),"package:a.meta")
    b,_=metadata.definitions(model_meta(),"package:b.meta")
    different,_=metadata.definitions(model_meta(txd="different"),"comparison:c.meta")
    assert metadata.collisions(a+b)[0]["code"]=="duplicate_definition"
    assert metadata.collisions(a+different)[0]["code"]=="conflicting_definition"
    override=metadata.collisions(a,different)[0]
    assert override["code"]=="external_override_candidate" and override["status"]=="warning"


def test_hash_collisions_and_numeric_kit_identity_are_checked(monkeypatch):
    monkeypatch.setattr(metadata,"joaat",lambda _:123)
    a,_=metadata.definitions(model_meta("first"),"a")
    b,_=metadata.definitions(model_meta("second"),"b")
    assert metadata.collisions(a+b)[0]["code"]=="hash_collision"
    kit=b'<CVehicleModelInfoVarGlobal><Kits><Item><kitName>kit</kitName><id value="12"/></Item></Kits></CVehicleModelInfoVarGlobal>'
    records,_=metadata.definitions(kit,"carcols.meta")
    assert records[1]["namespace"]=="tuning_kit_id" and records[1]["key"]=="12"


@pytest.mark.parametrize("data", [b'<!DOCTYPE x SYSTEM "file:///canary"><x/>', model_meta(name=""), b'x'*(8*1024**2+1)], ids=["dtd","empty-identity","oversized"])
def test_unsafe_or_unbounded_metadata_is_rejected(data):
    with pytest.raises(ValueError): metadata.definitions(data,"input.meta")


def test_folder_report_combines_models_metadata_and_exact_coverage(tmp_path):
    root=tmp_path/"package";root.mkdir()
    (root/"body.ydr.xml").write_bytes(FIXTURE.read_bytes())
    (root/"vehicles.meta").write_bytes(model_meta())
    (root/"conflict.meta").write_bytes(model_meta(txd="other"))
    (root/"packed.rpf").write_bytes(b"not opened")
    before={p.name:p.read_bytes() for p in root.iterdir()}
    report=package.inspect(str(root))
    assert report["static_status"]=="fail" and report["runtime_status"]=="not_tested"
    assert any(f["code"]=="conflicting_definition" for f in category(report,"metadata")["findings"])
    assert any(f["code"]=="embedded_archive_unexpanded" for f in category(report,"skeleton")["findings"])
    assert len(report["files"])==4 and report["lod_metrics"][0]["source"]=="body.ydr.xml"
    assert _bounded(report)==report
    assert before=={p.name:p.read_bytes() for p in root.iterdir()}
    digest=report.pop("report_sha256")
    assert digest==desktop.digest(report)


def test_comparison_context_does_not_invent_load_order(tmp_path):
    root=tmp_path/"package";other=tmp_path/"comparison";root.mkdir();other.mkdir()
    (root/"vehicles.meta").write_bytes(model_meta())
    (other/"vehicles.meta").write_bytes(model_meta(txd="other"))
    report=package.inspect(str(root),comparison=str(other))
    assert category(report,"metadata")["status"]=="warning"
    assert report["comparison_definition_count"]==1
    assert report["runtime_status"]=="not_tested"


def test_native_without_edition_is_visible_as_unchecked(tmp_path):
    (tmp_path/"body.ydr").write_bytes(b"native-unopened")
    report=package.inspect(str(tmp_path))
    assert report["files"][0]["coverage"]=="not_checked"
    assert category(report,"skinning")["status"]=="not_checked"


def test_data_tool_review_exports_one_report_and_rejects_stale_context(tmp_path):
    root=tmp_path/"package";root.mkdir();(root/"vehicle.meta").write_bytes(model_meta())
    context={"module":"data_tools","task":"asset_validation","source":str(root)}
    session=desktop.inspect(context)
    request={**context,"action":"export","destination":str(tmp_path/"reports"),"expected_state_sha256":session["state_sha256"]}
    review=desktop.review(request)
    result=desktop.apply({**request,"review_sha256":review["review_sha256"],"authoring_confirmed":True})
    assert result["game_write_performed"] is False
    assert json.loads((tmp_path/"reports/asset-validation.json").read_bytes())==session["document"]
    (root/"vehicle.meta").write_bytes(model_meta("changed"))
    with pytest.raises(ValueError,match="changed"):
        desktop.review({**request,"destination":str(tmp_path/"other-report")})
