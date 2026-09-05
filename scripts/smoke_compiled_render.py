"""Real native decode -> headless Blender -> reviewed PNG export, no game files."""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile

from PIL import Image

from allin1_sdk.desktop_protocol import dispatch_operation
from allin1_sdk.paths import project_root
from allin1_sdk.processes import run_hidden


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender", required=True)
    parser.add_argument("--evidence-directory")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="allin1-render-smoke-") as temporary:
        root = Path(temporary)
        source = root / "Source with spaces"
        source.mkdir()
        model = source / "fixture.ydr"
        native = project_root() / "tools" / "RpfPatcher" / "RpfPatcher.exe"
        completed = run_hidden([str(native), "asset-from-xml", str(project_root() / "tests" / "fixtures" / "render_tetrahedron.ydr.xml"), str(model), str(source), "legacy"], capture_output=True, text=True)
        if completed.returncode:
            raise RuntimeError(completed.stderr or completed.stdout)
        os.environ["ALLIN1_PREVIEW_DIR"] = str(root / "preview cache")
        _, rendered = dispatch_operation("inspect_authoring_workspace", {"module": "render", "source": str(model),
            "edition": "Legacy", "render": True, "blender_executable": args.blender,
            "settings": {"width": 512, "height": 512, "samples": 8, "quality": "preview", "engine": "cycles", "device": "cpu"}})
        request = {"module": "render", "action": "export", "render_id": rendered["render_id"],
                   "expected_state_sha256": rendered["state_sha256"], "destination": str(root / "verified.png")}
        _, review = dispatch_operation("review_workspace_action", request)
        _, receipt = dispatch_operation("apply_workspace_action", {**request, "authoring_confirmed": True, "review_sha256": review["review_sha256"]})
        with Image.open(receipt["output"]) as image:
            assert image.size == (512, 512)
            assert image.convert("RGB").getcolors(maxcolors=100) is None, "A blank image is not rendered-geometry evidence"
        metadata = rendered["render_record"]["metadata"]
        assert metadata["triangle_count"] == 4
        assert metadata["backend"] == "Blender headless"
        if args.evidence_directory:
            from allin1_sdk.workspace_desktop import path
            evidence = path(args.evidence_directory, new=True, writable=True)
            evidence.mkdir()
            shutil.copyfile(receipt["output"], evidence / "verified.png")
            shutil.copyfile(receipt["receipt"], evidence / "verified.png.render.json")
        print(json.dumps({"status": "PASS", "render_identity": rendered["render_id"], "source_and_runtime": rendered["render_record"]["identity"],
                          "metadata": metadata, "png_sha256": receipt["output_sha256"],
                          "game_acceptance": "NOT TESTED", "real_installation_modified": False}, indent=2))


if __name__ == "__main__":
    main()
