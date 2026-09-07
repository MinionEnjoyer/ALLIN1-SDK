from pathlib import Path
import json
from types import SimpleNamespace

from lxml import etree
import pytest

from scripts import retail_asset_report
from scripts.retail_asset_report import inspect_pair


def test_retail_report_workflow_uses_exact_explicit_rig_and_preserves_inputs(tmp_path):
    model_dir = tmp_path / "model"; model_dir.mkdir()
    rig_dir = tmp_path / "rig"; rig_dir.mkdir()
    data = (Path(__file__).parent / "fixtures/animation_skin.ydr.xml").read_bytes()
    rig = rig_dir / "rig.ydr.xml"; rig.write_bytes(data)
    document = etree.fromstring(data); document.remove(document.find("Skeleton"))
    model = model_dir / "body.ydr.xml"; model.write_bytes(etree.tostring(document))
    original = model.read_bytes()
    initial, selected = inspect_pair(model, rig, "Enhanced")
    assert not initial["shared_rigs"] and len(selected["shared_rigs"]) == 1
    assert next(row for row in selected["checks"] if row["category"] == "skinning")["status"] == "pass"
    assert selected["runtime_status"] == "not_tested"
    assert model.read_bytes() == original and rig.read_bytes() == data


@pytest.mark.parametrize("counts", [{"numTotalTests":1,"numPassedTests":0,"success":True},
                                   {"numTotalTests":0,"numPassedTests":0,"success":True}])
def test_skipped_or_missing_react_execution_is_not_a_pass(tmp_path, monkeypatch, counts):
    monkeypatch.setattr(retail_asset_report, "inspect_pair", lambda *args: ({}, {}))
    monkeypatch.setattr(retail_asset_report.shutil, "which", lambda name: "node")
    def run(command, **kwargs):
        Path(command[-1].split("=",1)[1]).write_text(json.dumps(counts))
        return SimpleNamespace(returncode=0, stderr="", stdout="")
    monkeypatch.setattr(retail_asset_report.subprocess, "run", run)
    with pytest.raises(ValueError, match="did not actually execute"):
        retail_asset_report.qualify(tmp_path / "model", tmp_path / "rig", "Enhanced")
