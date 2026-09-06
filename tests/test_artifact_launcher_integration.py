"""Cross-repository contract proof using only disposable synthetic packages."""
import json
from pathlib import Path
import sys

import pytest

from allin1_sdk import artifact_contract, optimization_package
from test_optimization_package import request

LAUNCHER = Path(__file__).resolve().parents[2]/"ALLIN1"
pytestmark = pytest.mark.skipif(not (LAUNCHER/"src/allin1/sdk_provenance.py").is_file(), reason="matching Launcher source required")


def test_contract_is_shared_and_real_sdk_export_installs_with_exact_lineage(tmp_path, monkeypatch):
    assert Path(artifact_contract.__file__).read_text() == (LAUNCHER/"src/allin1/artifact_contract.py").read_text()
    monkeypatch.syspath_prepend(str(LAUNCHER/"src"))
    from allin1.mods import ModManifest, ModIntegrationService
    from allin1.sdk_provenance import file_hash
    payload = request(tmp_path)
    source = Path(payload["source"])
    (source/"mod.toml").write_text('schema_version = 1\nid = "sdk-trail-fixture"\nname = "SDK trail fixture"\nversion = "1.0.0"\ntype = "config"\neditions = ["legacy", "enhanced"]\n[[files]]\nsource = "assets/diffuse.dds"\ndestination = "scripts/fixture.dds"\n')
    session = optimization_package.inspect(payload)
    export = {**payload,"action":"export","destination":str(tmp_path/"export"),"expected_state_sha256":session["state_sha256"]}
    optimization_package.review(export)
    optimization_package.apply(export)
    artifact = json.loads((tmp_path/"export/package/sdk-artifact.json").read_text())
    game = tmp_path/"synthetic-game"; game.mkdir(); (game/"GTA5.exe").write_bytes(b"test-owned marker, not executable")
    service = ModIntegrationService(game)
    service.install(ModManifest.load(tmp_path/"export/package/mod.toml"))
    lineage = service._read_receipt("sdk-trail-fixture")["sdk_provenance"]
    assert lineage["artifact_id"] == artifact["artifact_id"]
    assert lineage["build_fingerprint"] == session["artifact_manifest"]["build"]["build_fingerprint"]
    assert lineage["files"][0]["sha256"] == file_hash(game/"scripts/fixture.dds")
    assert lineage["files"][0]["sha256"] == session["changes"][0]["after"]["sha256"]
    assert file_hash(source/"assets/diffuse.dds") == session["changes"][0]["before"]["sha256"]
