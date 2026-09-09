"""Exercise the real offline sight decoder against a local editable copy.

Only the caller's output folder is written. Retail assets/results stay local.
"""
import argparse
import hashlib
import json
from pathlib import Path

from allin1_sdk import weapon_desktop


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--workspace", required=True, type=Path)
    cli.add_argument("--output", required=True, type=Path)
    cli.add_argument("--edition", choices=("enhanced", "legacy"), required=True)
    cli.add_argument("--weapon", action="append", required=True)
    cli.add_argument("--animation", help="Exact bundled YCD to inventory for the first weapon")
    cli.add_argument("--high-detail", action="store_true", help="Require the exact _hi body and component files")
    cli.add_argument("--component", action="append", default=[], help="Exact component identity to test on its declared owner")
    args = cli.parse_args()
    workspace, output = args.workspace.resolve(strict=True), args.output.resolve()
    if output == workspace or output.is_relative_to(workspace):
        raise ValueError("Output must be outside the editable copy")
    output.mkdir(parents=True, exist_ok=False)
    before = hashes(workspace)
    report = []
    used_components = set()
    for name in args.weapon:
        request = {"workspace": str(workspace), "weapon": name}
        snapshot = weapon_desktop.inspect(request)
        body = next(p for p in snapshot["native_preview"]["parts"] if p["kind"] == "weapon")
        suffix = "_hi" if args.high_detail else ""
        assets = [a["path"] for a in body["assets"] if Path(a["path"]).stem.casefold() == body["model"].casefold() + suffix]
        if len(assets) != 1:
            raise ValueError("No unique base body asset; choose it manually")
        request.update(expected_revision=snapshot["revision"], edition=args.edition, sight_action="model", entry=assets[0], lod="High")
        result = weapon_desktop.inspect(request)
        (output / f"{name}-model.json").write_text(json.dumps(result), encoding="utf-8")
        (output / f"{name}-snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
        row = {"weapon": name, "entry": assets[0], "native_sha256": result["native_sha256"],
               "vertices": result["packet"]["vertex_count"], "triangles": result["packet"]["triangle_count"],
               "unavailable": result["packet"].get("view_unavailable") or result["packet"].get("binding_required")}
        report.append(row)
        print(json.dumps(row), flush=True)
        for part in snapshot["native_preview"]["parts"]:
            if part["kind"] != "component" or part["name"] not in args.component:
                continue
            choices = [a["path"] for a in part["assets"] if Path(a["path"]).stem.casefold() == part["model"].casefold() + suffix]
            if len(choices) != 1:
                raise ValueError("No unique requested component variant")
            assembled = weapon_desktop.inspect({**request, "component": part["name"], "component_entry": choices[0]})
            prefix = name + "__" + part["name"]
            (output / f"{prefix}-model.json").write_text(json.dumps(assembled), encoding="utf-8")
            (output / f"{prefix}-snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
            used_components.add(part["name"])
            report.append({"weapon": name, "component": part["name"], "native_sha256": assembled["attachment"]["native_sha256"],
                "vertices": assembled["packet"]["vertex_count"] + assembled["attachment"]["packet"]["vertex_count"],
                "unavailable": assembled["packet"].get("view_unavailable")})
        if name == args.weapon[0] and args.animation:
            request.update(sight_action="animation", entry=args.animation)
            result = weapon_desktop.inspect(request)
            (output / "animation-inventory.json").write_text(json.dumps(result), encoding="utf-8")
            usable = [c for c in result["packet"]["choices"] if c["error"] is None]
            if usable:
                selected = next((c for c in usable if "fire" in c["name"].lower()), usable[0])
                result = weapon_desktop.inspect({**request, "selection": selected["key"]})
                (output / "animation-samples.json").write_text(json.dumps(result), encoding="utf-8")
                print("Sampled " + selected["name"], flush=True)
    assert used_components == set(args.component), "A requested component was not found on any selected weapon"
    assert hashes(workspace) == before, "Editable copy was modified"
    (output / "smoke.json").write_text(json.dumps({"workspace_unchanged": True, "results": report}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
