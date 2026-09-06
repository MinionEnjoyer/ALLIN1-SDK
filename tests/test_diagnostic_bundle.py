import hashlib
import json

import pytest

from allin1_sdk import diagnostic_bundle as bundle,data_tools_desktop as desktop
from allin1_sdk.artifact_contract import digest
from test_diagnostic_trail import fixture


def settings(file,**changes):
    return {"logs":[{"source":str(file),"start_line":1,"line_count":50,**changes}],"redact_terms":["PrivateMachine"]}


def test_redaction_preserves_useful_game_relative_context_but_omits_private_inputs(tmp_path):
    game=tmp_path/"game";file=tmp_path/"private-owner.log"
    source=f'''session_start controller=4.5.0
loaded "{game}\\VehicleWorkbenchAxles.asi"
Config C:\\Users\\Someone\\private folder\\settings.json
Contact owner@example.com on PrivateMachine
Authorization: Bearer do-not-export
URL https://example.com/upload?secret=private
Host \\\\PrivateServer\\share\\file
IP 192.168.1.10
-----BEGIN PRIVATE KEY-----
contents-must-not-leak
-----END PRIVATE KEY-----
'''
    file.write_text(source)
    preview=bundle.inspect(settings(file),game)
    text=json.dumps(preview)
    for private in ("Someone","PrivateServer","private-owner","owner@example.com","PrivateMachine","do-not-export","contents-must-not-leak","192.168.1.10",str(game)):
        assert private not in text
    assert "VehicleWorkbenchAxles.asi" in text and "controller=4.5.0" in text
    assert preview["logs"][0]["redacted_lines"]>=8
    assert preview["logs"][0]["source_sha256"]==hashlib.sha256(file.read_bytes()).hexdigest()
    assert preview["preview_sha256"]==digest({k:v for k,v in preview.items() if k!="preview_sha256"})
    assert file.read_text()==source


def test_selected_range_inside_multiline_key_still_redacts_secret(tmp_path):
    file=tmp_path/"log.txt";file.write_text("-----BEGIN PRIVATE KEY-----\nvery-secret-key\n-----END PRIVATE KEY-----\nsafe\n")
    preview=bundle.inspect(settings(file,start_line=2,line_count=1),tmp_path)
    assert preview["logs"][0]["lines"]==[{"line":2,"text":"<redacted private key line>"}]


@pytest.mark.parametrize("fault",["extension","binary","size","duplicate","range","line_budget","terms"])
def test_bundle_rejects_unbounded_or_nontext_inputs(tmp_path,fault):
    file=tmp_path/("asset.ydr" if fault=="extension" else "input.log");file.write_text("hello\n")
    selection=settings(file)
    if fault=="binary":file.write_bytes(b"MZ\x00\x01")
    elif fault=="size":file.write_bytes(b"a"*(bundle.MAX_FILE+1))
    elif fault=="duplicate":selection["logs"]*=2
    elif fault=="range":selection["logs"][0]["start_line"]=0
    elif fault=="line_budget":selection["logs"][0]["line_count"]=513
    elif fault=="terms":selection["redact_terms"]=["x"]*17
    with pytest.raises(ValueError):bundle.inspect(selection,tmp_path)


def test_utf16_and_line_clipping_are_explicit(tmp_path):
    file=tmp_path/"input.log";file.write_bytes(("hello\n"+"x"*5000).encode("utf-16"))
    result=bundle.inspect(settings(file),tmp_path)["logs"][0]
    assert result["included_lines"]==2 and result["truncated_lines"]==1
    assert result["lines"][1]["text"].endswith("[line truncated]")


def test_review_binds_exact_redacted_bytes_and_exports_a_verifiable_local_bundle(tmp_path):
    payload,_,_=fixture(tmp_path)
    file=tmp_path/"script.log";file.write_text("version=1.4.0\nPrivateMachine\npassword=never-export\n")
    payload={**payload,"task":"diagnostic_trail","settings":settings(file)}
    result=desktop.inspect(payload)
    destination=tmp_path/"bundle"
    request={**payload,"action":"export","destination":str(destination),"expected_state_sha256":result["state_sha256"]}
    with pytest.raises(ValueError,match="acknowledge"):desktop.review(request)
    request["privacy_review_sha256"]=result["document"]["log_bundle"]["preview_sha256"]
    desktop.review(request);receipt=desktop.apply(request)
    assert set(receipt["outputs"])=={"diagnostic-trail.json","diagnostic-bundle.json","log-01.txt"}
    exported=json.loads((destination/"diagnostic-bundle.json").read_text())
    assert exported["bundle_sha256"]==digest({k:v for k,v in exported.items() if k!="bundle_sha256"})
    for name,checksum in exported["files"].items():assert hashlib.sha256((destination/name).read_bytes()).hexdigest()==checksum
    assert "never-export" not in (destination/"log-01.txt").read_text()
    assert exported["diagnostic_state_sha256"]==result["state_sha256"]
    file.write_text(file.read_text()+"new session\n")
    with pytest.raises(ValueError,match="changed"):desktop.review({**request,"destination":str(tmp_path/"second")})
    assert not (tmp_path/"second").exists()
