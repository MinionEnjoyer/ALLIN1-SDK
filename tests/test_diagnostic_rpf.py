import json
import os
import shutil

import pytest

from allin1_sdk import diagnostic_rpf, diagnostic_trail
from allin1_sdk.artifact_identity import manifest
from allin1_sdk.workspace_desktop import file_hash
from test_artifact_identity import build
from test_package_intake import native_archive


def receipt_for(checksum, edition="Legacy", entry="vehicles.rpf!vehicles.meta"):
    artifact=manifest(build(),{"original.meta":"a"*64},{"vehicles.meta":checksum},edition=edition)
    member={"source":"vehicles.meta","archive":"mods/dlc.rpf","entry":entry,"sha256":checksum}
    receipt={"enabled":True,"files":[],"rpf_entries":[{key:member[key] for key in ("archive","entry","sha256")}],
             "sdk_provenance":{"artifact":artifact,"artifact_id":artifact["artifact_id"],"build_fingerprint":artifact["build"]["build_fingerprint"],"files":[],"rpf_members":[member]}}
    return artifact,receipt


@pytest.mark.parametrize("mutation",["disabled","edition","missing"])
def test_unavailable_or_disabled_member_is_not_misreported_as_active(tmp_path,mutation):
    artifact,receipt=receipt_for("a"*64,None if mutation=="edition" else "Legacy")
    if mutation=="disabled": receipt["enabled"]=False
    result=diagnostic_rpf.inspect(tmp_path,receipt,artifact,artifact)
    assert result[0]["status"]==("missing_archive" if mutation=="missing" else "not_checked")
    assert result[0]["actual_sha256"] is None


@pytest.mark.parametrize("mutation",["duplicate","traversal","mapping","stock"])
def test_receipt_targets_are_validated_before_archive_access(tmp_path,mutation):
    artifact,receipt=receipt_for("a"*64)
    members=receipt["sdk_provenance"]["rpf_members"]
    if mutation=="duplicate": members.append(dict(members[0]))
    elif mutation=="traversal": members[0]["entry"]="vehicles.rpf!../outside.meta"
    elif mutation=="mapping": members[0]["sha256"]="b"*64
    else: members[0]["archive"]="update/update.rpf"
    with pytest.raises(ValueError): diagnostic_rpf.inspect(tmp_path,receipt,artifact,artifact)


@pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST")!="1",reason="explicit native helper gate")
@pytest.mark.parametrize("edition",["Legacy","Enhanced"])
def test_actual_nested_installed_member_is_rechecked_without_game_mutation(tmp_path,edition):
    archive,game=native_archive(tmp_path,edition)
    (game/"mods").mkdir();installed=game/"mods/dlc.rpf";shutil.copyfile(archive,installed)
    checksum=file_hash(tmp_path/"source/vehicles.rpf.source/vehicles.meta")
    artifact,receipt=receipt_for(checksum,edition)
    artifact_file=tmp_path/"artifact.json";artifact_file.write_text(json.dumps(artifact))
    receipt_file=tmp_path/"receipt.json";receipt_file.write_text(json.dumps(receipt))
    payload={"source":str(artifact_file),"comparison":str(receipt_file),"gta_path":str(game)}
    original=file_hash(installed)
    report=diagnostic_trail.inspect(payload)
    row=report["rpf_members"][0]
    assert row["status"]=="match",row
    assert row["actual_sha256"]==checksum and row["container_sha256"]==original
    assert any(f["code"]=="installed_rpf_member_match" for f in report["findings"])
    assert file_hash(installed)==original
    assert str(game) not in json.dumps(report)
    # Same installed member, but the selected SDK artifact is a different build.
    newer=manifest(build(),{"original.meta":"a"*64},{"vehicles.meta":"b"*64},edition=edition)
    artifact_file.write_text(json.dumps(newer))
    report=diagnostic_trail.inspect(payload)
    assert report["rpf_members"][0]["status"]=="mismatch"
    assert report["crash_cause"]=="not_established"
    # An exact-but-absent member must not fall back to another similarly named file.
    artifact,receipt=receipt_for(checksum,edition,entry="vehicles.rpf!absent.meta")
    receipt_file.write_text(json.dumps(receipt));artifact_file.write_text(json.dumps(artifact))
    report=diagnostic_trail.inspect(payload)
    assert report["rpf_members"][0]["status"]=="missing_member"
    assert file_hash(installed)==original
