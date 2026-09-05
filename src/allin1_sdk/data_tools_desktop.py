"""Read-only metadata tools with reviewed, exclusive report-directory export."""
import json
import tempfile
from pathlib import Path

from allin1_sdk.workspace_desktop import _inventory, digest, file_hash, path

OUTPUTS = {
    "meta_diff": ["meta-diff.md", "meta-diff.json"],
    "meta_roundtrip": ["meta-roundtrip.json"],
    "vehicle_data": ["vehicles.json", "vehicles.csv", "unresolved.csv", "vehicles.xlsx", "vehicle-data-report.md"],
    "dlc_inventory": ["dlc-inventory.md", "dlc-inventory.json"],
}


def _report(payload):
    task = payload.get("task")
    if task not in OUTPUTS:
        raise ValueError("Choose a supported data tool")
    source = path(payload.get("source"))
    if task == "dlc_inventory":
        from allin1_sdk.dlc_inventory import DlcInventory
        if not source.is_dir():
            raise ValueError("Choose a GTA folder for DLC inventory")
        # Only the explicitly selected installation is scanned; never autodetect.
        from allin1_sdk.paths import project_root
        report = DlcInventory(project_root()).scan(source)
        document = report.to_dict()
        fingerprint = digest(document)
    else:
        def identity():
            if source.is_dir():
                return _inventory(source)
            if source.stat().st_size > 512 * 1024**2:
                raise ValueError("Data tool input exceeds 512 MiB")
            return file_hash(source)
        before = identity()
        if task == "vehicle_data":
            from allin1_sdk.rage_data_compiler import RageVehicleDataCompiler
            from allin1_sdk.addon_importer import AddonPackageInspector
            from allin1_sdk.paths import project_root
            game = path(payload["gta_path"]) if payload.get("gta_path") else None
            report = RageVehicleDataCompiler.compile_scan(AddonPackageInspector(project_root(), game).inspect(source))
            document = report.to_dict()
        elif task == "meta_diff":
            from allin1_sdk.meta_tools import diff_meta
            comparison = path(payload.get("comparison"))
            other_hash = file_hash(comparison)
            report = diff_meta(source, comparison)
            document = report.to_dict()
            document.pop("generated_at", None)
            if file_hash(comparison) != other_hash:
                raise ValueError("Comparison input changed during inspection")
            before = {"source": before, "comparison": other_hash}
        else:
            from allin1_sdk.meta_tools import validate_meta_roundtrip
            document = report = validate_meta_roundtrip(source)
            document.pop("generated_at", None)
        current = identity()
        if (before["source"] if task == "meta_diff" else before) != current:
            raise ValueError("Input changed during inspection")
        fingerprint = digest({"inputs": before, "task": task, "document": document})
    if len(json.dumps(document).encode()) > 256 * 1024:
        raise ValueError("Data report exceeds the desktop result limit; narrow the input")
    return source, report, document, fingerprint


def inspect(payload):
    source, _, document, fingerprint = _report(payload)
    return {"source": str(source), "task": payload["task"], "document": document,
            "state_sha256": fingerprint, "outputs": OUTPUTS[payload["task"]]}


def review(payload):
    if payload.get("action") != "export":
        raise ValueError("Data tools support report export only")
    result = inspect(payload)
    if result["state_sha256"] != payload.get("expected_state_sha256"):
        raise ValueError("Data input changed; inspect again before exporting")
    destination = path(payload.get("destination"), new=True, writable=True)
    source = Path(result["source"])
    if source.is_dir() and destination.is_relative_to(source):
        raise ValueError("Reports must be outside the input tree")
    return {**result, "action": "export", "destination": str(destination)}


def apply(payload):
    source, report, document, fingerprint = _report(payload)
    if fingerprint != payload.get("expected_state_sha256"):
        raise ValueError("Data input changed before export")
    destination = path(payload.get("destination"), new=True, writable=True)
    task = payload["task"]
    # Domain writers run in a private directory; publish only complete reports.
    with tempfile.TemporaryDirectory(prefix=".allin1-data-", dir=destination.parent) as temporary:
        staged = Path(temporary)
        if task == "vehicle_data":
            report.write_bundle(staged)
        elif task == "dlc_inventory":
            report.write(staged / "dlc-inventory.md")
        elif task == "meta_diff":
            report.write(staged / "meta-diff.md")
            (staged / "meta-diff.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
        else:
            (staged / "meta-roundtrip.json").write_text(json.dumps(document, indent=2), encoding="utf-8")
        inventory = _inventory(staged)
        if set(inventory) != set(OUTPUTS[task]):
            raise ValueError("Generated data report does not match its declared outputs")
        # Exclusive directory creation prevents replacing an existing report tree.
        path(str(destination), new=True, writable=True).mkdir()
        for name in inventory:
            (staged / name).replace(destination / name)
    return {"destination": str(destination), "outputs": inventory, "source": str(source)}
