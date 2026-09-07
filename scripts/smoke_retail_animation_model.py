"""Opt-in local retail ped binding check. Never builds or writes game resources.

Only summaries/hashes are printed. Extracted retail resources and exported XML
live in a TemporaryDirectory and are not fixtures or redistribution artifacts.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from allin1_sdk import animation_model
from allin1_sdk.desktop_protocol import _bounded
from allin1_sdk.native_assets import NativeAssetInspector
from allin1_sdk.paths import project_root
from allin1_sdk.rpf_tools import RpfExplorerService


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(game: Path, edition: str, *, asset_report: bool = False):
    game = game.resolve(strict=True)
    archive = game / "x64v.rpf"
    before = sha(archive)
    print(json.dumps({"phase": "index", "edition": edition, "archive_sha256": before}), flush=True)
    service = RpfExplorerService(project_root(), game)
    index = service.index(archive)
    if index.edition.casefold() != edition.casefold():
        raise ValueError("Detected edition differs from explicit requested edition")
    entries = {
        "model": "models/cdimages/streamedpeds_mp.rpf::mp_m_freemode_01/uppr_000_r.ydd",
        "skeleton": "models/cdimages/streamedpeds_mp.rpf::mp_m_freemode_01.yft",
    }
    inspector = NativeAssetInspector(project_root(), game)
    results = []
    try:
        with tempfile.TemporaryDirectory(prefix="allin1-retail-ped-") as temporary:
            root = Path(temporary)
            xml = {}
            for kind, entry_id in entries.items():
                entry = index.entry(entry_id)
                if entry.size > 16 * 1024**2:
                    raise ValueError("Selected retail fixture exceeds the bounded extraction size")
                source = service.extract(index, entry, root / Path(entry.path).name)
                destination = root / kind
                inspector.export_workspace(source, destination, edition=edition)
                xml[kind] = destination / "edit" / f"{source.name}.xml"
                print(json.dumps({"phase": "export", "kind": kind, "entry": entry.id, "source_sha256": sha(source), "xml_bytes": xml[kind].stat().st_size}), flush=True)
            if asset_report:
                if __package__:
                    from .retail_asset_report import qualify
                else:
                    from retail_asset_report import qualify
                print(json.dumps(qualify(xml["model"], xml["skeleton"], edition)), flush=True)
            initial = animation_model.inspect(str(xml["model"]))
            if initial["selected"] is None:
                initial = animation_model.inspect(str(xml["model"]), drawable="0")
            for lod in initial["lods"]:
                try:
                    packet = animation_model.inspect(str(xml["model"]), drawable=initial["selected"], lod=lod,
                        skeleton_xml=str(xml["skeleton"]) if initial.get("binding_required") else None)
                    if packet.get("binding_required") or packet.get("view_unavailable"):
                        raise ValueError(packet.get("binding_required") or packet["view_unavailable"])
                    if _bounded({"session": {"animation_model": packet}})["session"]["animation_model"] != packet:
                        raise ValueError("Model evidence does not survive the desktop transport")
                    results.append({"lod": lod, "bones": len(packet["bones"]), "vertices": packet["vertex_count"], "triangles": packet["triangle_count"],
                        "shared_skeleton": bool(packet.get("skeleton_binding")), "status": "passed"})
                except ValueError as exc:
                    results.append({"lod": lod, "status": "blocked", "reason": str(exc)})
    finally:
        after = sha(archive)
        print(json.dumps({"phase": "source_verification", "unchanged": before == after, "archive_sha256": after}), flush=True)
        if before != after:
            raise RuntimeError("Source archive changed during read-only qualification")
    print(json.dumps({"edition": edition, "archive_bytes": archive.stat().st_size, "archive_entries": len(index.entries),
        "archive_encryptions": sorted({item.encryption for item in index.archives}), "results": results,
        "game_write_performed": False, "pose_rendering": "not_tested", "in_game_acceptance": "not_tested"}), flush=True)
    return 0 if results and all(item["status"] == "passed" for item in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--edition", choices=["Legacy", "Enhanced"], required=True)
    parser.add_argument("--asset-report", action="store_true", help="Also qualify the real package/shared-rig report through React")
    args = parser.parse_args()
    raise SystemExit(run(args.game, args.edition, asset_report=args.asset_report))
