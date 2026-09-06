"""Actual native archive -> shared texture workspace -> inert replacement plan."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from allin1_sdk import desktop_protocol, workspace_desktop
from allin1_sdk.paths import project_root
from allin1_sdk.processes import run_hidden
from test_texture_workspace import _workspace


@pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST") != "1", reason="Requires the built native RpfPatcher/CodeWalker toolchain")
@pytest.mark.parametrize("edition", ["Legacy", "Enhanced"])
def test_exact_archive_texture_edit_rebuild_and_plan_keep_original_provenance(tmp_path, edition):
    fixture = _workspace(tmp_path)
    payload_dir = tmp_path / "payload"
    payload_dir.mkdir()
    game = tmp_path / "decoder"
    game.mkdir()
    (game / ("GTA5.exe" if edition == "Legacy" else "GTA5_Enhanced.exe")).write_bytes(b"MZ-synthetic-decoder-context-only")
    patcher = project_root() / "tools/RpfPatcher/RpfPatcher.exe"

    def command(*args):
        result = run_hidden([str(patcher), *map(str, args)], capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stderr or result.stdout

    source = payload_dir / "fixture.ytd"
    command("asset-from-xml", fixture / "edit/vehicle.ytd.xml", source, fixture / "edit/assets", "legacy" if edition == "Legacy" else "gen9")
    archive = tmp_path / "dictionary.rpf"
    command("build-dlc", payload_dir, archive)
    original_archive = hashlib.sha256(archive.read_bytes()).hexdigest()
    context = {"module": "native", "archive": str(archive), "entry_id": "::fixture.ytd", "gta_path": str(game)}

    def apply(context, action, **fields):
        session = workspace_desktop.inspect(context)
        payload = {**context, "action": action, "expected_state_sha256": session["state_sha256"], **fields}
        review = workspace_desktop.review(payload)
        return workspace_desktop.apply({**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True})

    exported = apply(context, "export", destination=str(tmp_path / "editable"))
    root = Path(exported["session"]["workspace"])
    provenance = (root / "native-origin.json").read_bytes()
    binding = json.loads(provenance)
    assert binding["entry_id"] == "::fixture.ytd" and binding["outer_archive_sha256"] == original_archive
    for action, fields in [("rename", {"new_name": "paint"}), ("batch", {"batch_action": "convert", "texture_names": ["paint", "normal"], "output_format": "DXT5", "mip_levels": "full"})]:
        _, texture = desktop_protocol.dispatch_operation("inspect_texture_workspace", {"workspace": str(root)})
        request = {"workspace": str(root), "expected_state_sha256": texture["state_sha256"], "action": action,
                   "texture_name": "diffuse" if action == "rename" else "paint", **fields}
        _, review = desktop_protocol.dispatch_operation("review_texture_edit", request)
        desktop_protocol.dispatch_operation("apply_texture_edit", {**request, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    assert (root / "native-origin.json").read_bytes() == provenance
    fresh_context = {"module": "native", "workspace": str(root), "gta_path": str(game)}
    refreshed = workspace_desktop.inspect(fresh_context)
    assert refreshed["state_sha256"] != exported["session"]["state_sha256"]
    assert refreshed["archive_binding"] == binding
    planned = apply(fresh_context, "plan_replacement", destination=str(tmp_path / "texture-replacement.json"), document={"authorized_root": str(tmp_path)})
    assert planned["plan_status"] == "ready"
    assert not planned["archive_write_performed"]
    plan = json.loads(Path(planned["plan"]).read_text())
    assert plan["archive_path"] == "" and plan["entry"] == "fixture.ytd"
    assert Path(plan["archive"]) == archive and plan["archive_sha256"] == original_archive
    assert plan["original"]["sha256"] == binding["extracted_sha256"]
    validation = json.loads(Path(plan["native_workspace"]["validation_report"]).read_text())["validation"]
    assert validation["texture_payloads_match"] and validation["texture_payload_count"] == 2
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == original_archive
