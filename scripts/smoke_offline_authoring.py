"""Real native authoring lifecycle using owned, disposable, non-game fixtures.

The YMAP-named member is an opaque marker, not a renderable game placement.
This gate proves packaging, exact-byte preservation and edition routing only.
"""
from __future__ import annotations
import hashlib
import json
import shutil
from pathlib import Path
import tempfile

from allin1_sdk import workspace_desktop as desktop
from allin1_sdk.paths import project_root
from allin1_sdk.rpf_tools import RpfExplorerService
from allin1_sdk.mods import ModManifest


def apply(context, action, **extra):
    request = {**context, "action": action, **extra}
    if action not in {"create", "import_archive"}:
        request["expected_state_sha256"] = desktop.inspect(context)["state_sha256"]
    review = desktop.review(request)
    result = desktop.apply({**request, "review_sha256": review["review_sha256"], "authoring_confirmed": True})
    assert result["game_write_performed"] is False
    return result


def main():
    with tempfile.TemporaryDirectory(prefix="allin1-native-authoring-") as temporary:
        root = Path(temporary)
        game = root / "Synthetic Enhanced decoder context"
        game.mkdir()
        (game / "GTA5_Enhanced.exe").write_bytes(b"MZ-owned-fixture-not-executable")
        source = root / "Map source with spaces" / "dlc.rpf.source"
        source.mkdir(parents=True)
        marker = b"Owned opaque placement marker; not live game content."
        (source / "custom_map.ymap").write_bytes(marker)
        document = {
            "schema_version": 1, "id": "test.map", "package_id": "test.map", "name": "Offline fixture", "version": "1.0.0",
            "editions": ["legacy", "enhanced"],
            "streaming": {"pack_name": "custom_map", "ipls": ["custom_map"], "activation_radius": 300, "release_radius": 500},
            "levels": [{"id": "interior", "name": "Interior", "center": {"x": 0, "y": 0, "z": 0, "heading": 0}, "ipls": []}],
            "portals": [{"id": "entry", "name": "Entry", "mode": "both", "radius": 3, "one_way": False,
                         "from": {"level": "world", "position": {"x": 0, "y": 0, "z": 0, "heading": 0}},
                         "to": {"level": "interior", "position": {"x": 0, "y": 0, "z": 0, "heading": 180}}}], "garages": [],
        }
        descriptor = root / "maps.json"
        apply({"module": "maps"}, "create", destination=str(descriptor), document=document)
        context = {"module": "maps", "descriptor": str(descriptor)}
        snapshot = desktop.inspect({**context, "source": str(source.parent), "gta_path": str(game)})
        assert snapshot["inventory"]["summary"]["valid"]
        result = apply(context, "build", document=snapshot["document"], source=str(source.parent), gta_path=str(game), edition="enhanced", destination=str(root / "Map package"))
        manifest = ModManifest.load(Path(result["build"]["manifest"]))
        assert manifest.editions == ("enhanced",)
        service = RpfExplorerService(project_root(), game)
        archive = Path(result["build"]["payload"])
        index = service.index(archive)
        extracted = service.extract(index, index.entry("::custom_map.ymap"), root / "extracted.ymap")
        assert extracted.read_bytes() == marker
        assert (source / "custom_map.ymap").read_bytes() == marker
        binary_context = {"module": "binary", "archive": str(archive), "entry_id": "::custom_map.ymap", "gta_path": str(game)}
        original_archive = hashlib.sha256(archive.read_bytes()).hexdigest()
        binary_snapshot = desktop.inspect(binary_context)
        binary_review = {**binary_context, "action": "create", "destination": str(root / "binary-copy"), "expected_state_sha256": binary_snapshot["state_sha256"]}
        proposal = desktop.review(binary_review)
        binary_copy = desktop.apply({**binary_review, "review_sha256": proposal["review_sha256"], "authoring_confirmed": True})
        binary_workspace = {"module": "binary", "workspace": binary_copy["session"]["workspace"]}
        apply(binary_workspace, "patch", offset=0, expected_hex=marker[:1].hex(), replacement_hex="FF")
        apply(binary_workspace, "build", destination=str(root / "edited-member.ymap"))
        assert (root / "edited-member.ymap").read_bytes() == b"\xff" + marker[1:]
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == original_archive
        assert binary_copy["session"]["source_binding"]["entry_id"] == "::custom_map.ymap"

        imported = apply({"module": "graph", "archive": str(archive), "gta_path": str(game)}, "import_archive", destination=str(root / "Imported graph"))
        imported_context = {"module": "graph", "workspace": imported["session"]["workspace"]}
        assert imported["session"]["document"]["origin"]["type"] == "rpf_archive_import"
        apply(imported_context, "materialize", destination=str(root / "Imported loose tree"))
        assert (root / "Imported loose tree" / "custom_map.ymap").read_bytes() == marker

        sealed_folder = root / "Sealed source"
        sealed_folder.mkdir()
        shutil.copyfile(archive, sealed_folder / "sealed.rpf")
        sealed_document = desktop.inspect({"module": "graph", "source": str(sealed_folder)})["document"]
        sealed_graph = root / "sealed-graph.json"
        apply({"module": "graph"}, "create", document=sealed_document, destination=str(sealed_graph))
        sealed_id = next(node["id"] for node in sealed_document["nodes"] if node["type"] == "sealed_archive")
        expanded = apply({"module": "graph", "workspace": str(sealed_graph)}, "expand", node_id=sealed_id, gta_path=str(game))
        assert next(node for node in expanded["session"]["document"]["nodes"] if node["id"] == sealed_id)["type"] == "archive"
        apply({"module": "graph", "workspace": str(sealed_graph)}, "materialize", destination=str(root / "Expanded loose tree"))
        assert (root / "Expanded loose tree" / "sealed.rpf.source" / "custom_map.ymap").read_bytes() == marker
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == original_archive

        graph_document = desktop.inspect({"module": "graph", "source": str(source)})["document"]
        graph = root / "graph.json"
        apply({"module": "graph"}, "create", document=graph_document, destination=str(graph))
        graph_build = apply({"module": "graph", "workspace": str(graph)}, "build", gta_path=str(game), destination=str(root / "graph-output.rpf"))
        graph_index = service.index(graph_build["output"])
        assert service.extract(graph_index, graph_index.entry("::custom_map.ymap"), root / "graph-extracted.ymap").read_bytes() == marker

        program_document = desktop.inspect({"module": "program", "graph": str(graph), "template": "verified-build"})["document"]
        next(node for node in program_document["nodes"] if node["id"] == "build")["config"] = {"gta_path": str(game), "output": str(root / "flow-output.rpf")}
        program = root / "program.json"
        apply({"module": "program"}, "create", document=program_document, destination=str(program))
        flow = apply({"module": "program", "workspace": str(program)}, "run", destination=str(root / "flow-receipt.json"))
        assert flow["execution"]["status"] == "verified"
        flow_index = service.index(root / "flow-output.rpf")
        assert service.extract(flow_index, flow_index.entry("::custom_map.ymap"), root / "flow-extracted.ymap").read_bytes() == marker
        patcher = project_root() / "tools" / "RpfPatcher" / "RpfPatcher.exe"
        print(json.dumps({"status": "PASS", "checks": ["map-package-enhanced", "graph-native-build", "flow-native-build", "archive-binary-copy-patch-build", "rpf-graph-import", "sealed-rpf-expansion", "exact-member-preservation", "source-preservation"],
                          "native_component_sha256": {name: hashlib.sha256((patcher.parent / name).read_bytes()).hexdigest()
                                                      for name in ("RpfPatcher.exe", "RpfPatcher.dll", "CodeWalker.Core.dll")},
                          "game_launched": False, "real_installation_modified": False, "fixture": "opaque-owned-member"}, indent=2))


if __name__ == "__main__":
    main()
