"""Read-only retail YCD sampling against the MP male shared skeleton.

All resource bytes/XML stay in temporary local storage. No game resources are
rebuilt, no fixture data is committed, and the full source archives are hashed
before and after. This does not constitute in-game animation acceptance.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from allin1_sdk import animation_model, animation_samples
from allin1_sdk.native_assets import NativeAssetInspector
from allin1_sdk.paths import project_root
from allin1_sdk.rpf_tools import RpfExplorerService
from smoke_retail_animation_model import sha


def run(game, edition, entry_id, all_clips=False):
    game = game.resolve(strict=True)
    archives = {name: game / name for name in ("x64c.rpf", "x64v.rpf")}
    before = {name: sha(file) for name, file in archives.items()}
    print(json.dumps({"phase": "start", "edition": edition, "source_archives": before}), flush=True)
    service = RpfExplorerService(project_root(), game)
    inspector = NativeAssetInspector(project_root(), game)
    try:
        indices = {name: service.index(file) for name, file in archives.items()}
        if any(index.edition.casefold() != edition.casefold() for index in indices.values()):
            raise ValueError("Detected installation does not match the selected edition")
        with tempfile.TemporaryDirectory(prefix="allin1-retail-animation-") as temporary:
            root = Path(temporary)
            resources = {"animation": ("x64c.rpf", entry_id),
                "model": ("x64v.rpf", "models/cdimages/streamedpeds_mp.rpf::mp_m_freemode_01/uppr_000_r.ydd"),
                "skeleton": ("x64v.rpf", "models/cdimages/streamedpeds_mp.rpf::mp_m_freemode_01.yft")}
            xml = {}
            for kind, (archive_name, key) in resources.items():
                index = indices[archive_name]
                entry = index.entry(key)
                if entry.kind == "directory" or entry.size > 16 * 1024**2:
                    raise ValueError("Retail test entry exceeds the 16 MiB extraction bound")
                source = service.extract(index, entry, root / Path(entry.path).name)
                inspector.export_workspace(source, root / kind, edition=edition)
                xml[kind] = root / kind / "edit" / f"{source.name}.xml"
                print(json.dumps({"phase": "export", "kind": kind, "entry": key, "source_sha256": sha(source), "xml_bytes": xml[kind].stat().st_size}), flush=True)
            model = animation_model.inspect(str(xml["model"]), drawable="0", skeleton_xml=str(xml["skeleton"]))
            if model.get("binding_required") or model.get("view_unavailable"):
                raise ValueError(model.get("binding_required") or model["view_unavailable"])
            animation_xml = xml["animation"].read_bytes()
            packet = animation_samples.analyze(animation_xml)
            tags = {bone["tag"] for bone in model["bones"]}
            matching = [track for track in packet["tracks"] if track["bone_tag"] in tags and track["track"] in (0, 1, 2, 5, 6)]
            print(json.dumps({"phase": "sampled", "selected": packet["selected"], "duration": packet["duration"], "choices": packet["choices"][:20],
                "samples": len(packet["times"]), "tracks": len(packet["tracks"]), "matching_trs_root_tracks": len(matching),
                "track_types": sorted({track["track"] for track in packet["tracks"]}), "flag_values": sorted({track["flags"] for track in packet["tracks"]}),
                "bones": len(model["bones"]), "vertices": model["vertex_count"], "pose_rendering": "not_tested", "game_acceptance": "not_tested"}), flush=True)
            if not matching:
                raise ValueError("Retail animation has no supported tracks matching the chosen rig")
            # Exercise the actual TypeScript skinning and React canvas-command
            # path using ephemeral retail packets, never committed fixtures.
            evidence = root / "retail-packet.json"
            node = shutil.which("node")
            if not node:
                raise RuntimeError("Node is required for retail React pose qualification")
            desktop = project_root() / "desktop"
            selections = [choice["key"] for choice in packet["choices"] if choice["kind"] == "clip" and not choice.get("error")] if all_clips else [packet["selected"]]
            if not selections or len(selections) > 16:
                raise ValueError("Retail smoke requires 1–16 selected clips")
            for selection in selections:
                selected_packet = packet if selection == packet["selected"] else animation_samples.analyze(animation_xml, selection)
                evidence.write_text(json.dumps({"model": model, "animation": selected_packet}), encoding="utf-8")
                print(json.dumps({"phase": "react_qualification", "selection": selection}), flush=True)
                subprocess.run([node, str(desktop / "node_modules/vitest/vitest.mjs"), "run", "--config", "vite.config.ts",
                                "RetailAnimation.integration.test.tsx"], cwd=desktop,
                               env={**os.environ, "ALLIN1_RETAIL_ANIMATION_PACKET": str(evidence)}, check=True, timeout=90)
    finally:
        after = {name: sha(file) for name, file in archives.items()}
        print(json.dumps({"phase": "source_verification", "unchanged": before == after, "source_archives": after}), flush=True)
        if before != after:
            raise RuntimeError("An input archive changed during read-only qualification")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--edition", choices=["Legacy", "Enhanced"], required=True)
    parser.add_argument("--entry", default="anim/ingame/clip_move_.rpf::move_m@casual@a.ycd")
    parser.add_argument("--all-clips", action="store_true", help="Qualify each valid clip in the dictionary (maximum 16)")
    args = parser.parse_args()
    run(args.game, args.edition, args.entry, args.all_clips)
