import os
import hashlib
from pathlib import Path
import shutil

import pytest

from allin1_sdk import package_intake, package_validation
from allin1_sdk.paths import project_root
from allin1_sdk.processes import run_hidden
from allin1_sdk.rpf_builder import RpfArchiveBuilder
from allin1_sdk.workspace_desktop import file_hash, digest
from test_texture_validation import package_fixture


def test_unexpanded_archive_and_source_hash_are_explicit(tmp_path):
    archive=tmp_path/"dlc.rpf";archive.write_bytes(b"RPF7-not-a-complete-archive")
    original=archive.read_bytes()
    report=package_validation.inspect(str(archive))
    assert report["source_kind"]=="rpf_archive"
    assert report["source_sha256"]==file_hash(archive)
    assert report["archive_provenance"]["package"]["archives"][0]["status"]=="unexpanded"
    assert report["static_status"]=="incomplete"
    assert all(any(f["code"]=="embedded_archive_unexpanded" for f in check["findings"]) for check in report["checks"])
    assert archive.read_bytes()==original


def test_changed_original_is_detected_after_materialized_inspection(tmp_path):
    archive=tmp_path/"dlc.rpf";archive.write_bytes(b"RPF7")
    with pytest.raises(ValueError,match="changed during"):
        with package_intake.materialize(archive) as (root,provenance):
            assert root!=tmp_path
            archive.write_bytes(b"changed")


def test_staging_copy_refuses_growth_before_writing_past_inventory_bound(tmp_path):
    source = tmp_path / "source.rpf"
    source.write_bytes(b"inventory-plus-later-growth")
    target = tmp_path / "staged.rpf"

    with pytest.raises(ValueError, match="size changed while staging"):
        package_intake._copy_inventory_file(
            source, target, 9, hashlib.sha256(b"inventory").hexdigest(),
        )

    # The helper copies no more than the size captured during inventory. The
    # temporary materialization root is discarded on this error in production.
    assert target.read_bytes() == b"inventory"


def native_archive(tmp_path,edition,size=1):
    package=package_fixture(tmp_path)
    if size!=1:
        from PIL import Image
        Image.new("RGBA",(size,size),(42,85,119,255)).save(package/"assets/diffuse.dds",format="DDS")
        xml=package/"paint.ytd.xml"
        xml.write_text(xml.read_text().replace('Width value="1"',f'Width value="{size}"').replace('Height value="1"',f'Height value="{size}"'))
    source=tmp_path/"source";source.mkdir()
    nested=source/"vehicles.rpf.source"
    shutil.copytree(package,nested)
    helper=project_root()/"tools/RpfPatcher/RpfPatcher.exe"
    result=run_hidden([str(helper),"asset-from-xml",str(nested/"paint.ytd.xml"),str(nested/"paint.ytd"),str(nested/"assets"),"legacy" if edition=="Legacy" else "gen9"],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr or result.stdout
    (nested/"paint.ytd.xml").unlink()
    result=run_hidden([str(helper),"asset-from-xml",str(nested/"car.ydr.xml"),str(nested/"car.ydr"),str(nested/"assets"),"legacy" if edition=="Legacy" else "gen9"],capture_output=True,text=True,timeout=60)
    assert result.returncode==0,result.stderr or result.stdout
    (nested/"car.ydr.xml").unlink()
    game=tmp_path/"decoder";game.mkdir()
    (game/("GTA5.exe" if edition=="Legacy" else "GTA5_Enhanced.exe")).write_bytes(b"MZ-test-owned-not-executable")
    archive,_=RpfArchiveBuilder(project_root(),game).build(source,tmp_path/"dlc.rpf")
    return archive,game


@pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST")!="1",reason="explicit native helper gate")
@pytest.mark.parametrize("edition",["Legacy","Enhanced"])
def test_real_recursive_archive_validation_keeps_exact_member_provenance(tmp_path,edition):
    archive,game=native_archive(tmp_path,edition)
    before=file_hash(archive)
    report=package_validation.inspect(str(archive),edition=edition,gta_path=str(game))
    provenance=report["archive_provenance"]["package"]
    assert provenance["archives"][0]["status"]=="expanded",provenance
    assert provenance["archives"][0]["nested_archives"]==1
    member=next(row for row in provenance["members"] if row["entry_path"]=="paint.ytd")
    assert member["container_sha256"]==before
    assert member["entry_id"]=="vehicles.rpf::paint.ytd"
    assert member["path"]=="dlc.rpf.source/vehicles.rpf.source/paint.ytd"
    texture_check=next(c for c in report["checks"] if c["category"]=="textures")
    assert texture_check["status"]==("not_checked" if edition=="Enhanced" else "pass")
    if edition=="Enhanced":
        assert {f["code"] for f in texture_check["findings"]}=={"unbound_texture_slot"}
    assert len(report["texture_resolutions"])==1
    assert report["texture_storage_bytes"]==4
    assert file_hash(archive)==before
    expected=report.pop("report_sha256")
    assert expected==digest(report)
