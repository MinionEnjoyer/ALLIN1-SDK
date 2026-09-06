"""Real converter/native desktop lifecycle using SDK-owned tetrahedron fixtures.

No installed game files are read or written. Synthetic decoder directories only
select the edition for unencrypted generated archives. Not in-game acceptance.
"""
from pathlib import Path
import hashlib
import json
import tempfile
import re

from allin1_sdk import workspace_desktop as desktop
from allin1_sdk.paths import project_root
from allin1_sdk.processes import run_hidden


def apply(context, action, **fields):
    session = desktop.inspect(context)
    request = {**context, "action": action, "expected_state_sha256": session["state_sha256"], **fields}
    review = desktop.review(request)
    return desktop.apply({**request, "review_sha256": review["review_sha256"], "authoring_confirmed": True})


def main():
    native = project_root() / "tools/RpfPatcher/RpfPatcher.exe"
    fixture = project_root() / "tests/fixtures/render_tetrahedron.ydr.xml"
    def command(*args):
        result = run_hidden([str(native), *map(str, args)], capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
    with tempfile.TemporaryDirectory(prefix="allin1-native-react-") as temporary:
        root = Path(temporary)
        for edition in ("Legacy", "Enhanced"):
            selected = root / edition
            selected.mkdir()
            loose = selected / "loose"
            loose.mkdir()
            source = loose / "fixture.ydr"
            command("asset-from-xml", fixture, source, loose, "legacy" if edition == "Legacy" else "gen9")
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            context = {"module": "native", "source": str(source), "edition": edition}
            result = apply(context, "export", destination=str(selected / "workspace"))
            workspace = {"module": "native", "workspace": result["session"]["workspace"]}
            built = apply(workspace, "build", destination=str(selected / "rebuilt.ydr"))
            assert built["validation"]["reparsed"] is True
            from allin1_sdk.artifact_contract import validate_manifest
            receipt = json.loads(Path(built["validation_report"]).read_bytes())
            artifact = validate_manifest(receipt["artifact"])
            assert artifact["outputs"] == {"rebuilt.ydr": built["output_sha256"]}
            assert built["provenance"]["build_fingerprint"] == artifact["build"]["build_fingerprint"]
            assert built["session"]["edition"] == edition
            assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
            game = selected / "Synthetic decoder"
            game.mkdir()
            (game / ("GTA5.exe" if edition == "Legacy" else "GTA5_Enhanced.exe")).write_bytes(b"MZ-synthetic-not-executable")
            archive = selected / "fixture.rpf"
            command("build-dlc", loose, archive)
            archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
            archived = apply({"module": "native", "archive": str(archive), "entry_id": "::fixture.ydr", "gta_path": str(game)}, "export", destination=str(selected / "archive-workspace"))
            bound = {"module": "native", "workspace": archived["session"]["workspace"], "gta_path": str(game)}
            xml = "".join(archived["session"]["xml_chunks"])
            edited, count = re.subn(r'(<LodDistHigh value=")[^"]+', r'\g<1>125', xml)
            assert count == 1
            apply(bound, "save_xml", document={"language": "xml", "chunks": [edited[i:i+8192] for i in range(0, len(edited), 8192)]})
            plan = apply(bound, "plan_replacement", destination=str(selected / "replacement.json"), document={"authorized_root": str(selected)})
            assert plan["archive_write_performed"] is False
            planned_artifact = validate_manifest(json.loads(Path(plan["validation_report"]).read_bytes())["artifact"])
            assert plan["provenance"]["artifact_id"] == planned_artifact["artifact_id"]
            assert plan["provenance"]["build_fingerprint"] == built["provenance"]["build_fingerprint"]
            assert plan["plan_status"] == "ready", plan
            assert hashlib.sha256(archive.read_bytes()).hexdigest() == archive_hash
            print(f"PASS {edition}: native export/build/reparse, exact archive export, reviewed replacement plan, unchanged originals", flush=True)
    print(json.dumps({"native_component_sha256": hashlib.sha256(native.read_bytes()).hexdigest(), "fixture": str(fixture), "game_acceptance": "not_tested"}))


if __name__ == "__main__":
    main()
