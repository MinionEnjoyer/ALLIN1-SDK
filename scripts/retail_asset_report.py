"""Report qualification on temporary retail XML; never copy it into fixtures."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from allin1_sdk import package_validation
from allin1_sdk.desktop_protocol import _bounded
from allin1_sdk.paths import project_root


def inspect_pair(model_xml: Path, skeleton_xml: Path, edition: str):
    """Use the same explicit selections and reports available in React."""
    options = {"comparison": str(skeleton_xml.parent), "edition": edition}
    initial = package_validation.inspect(str(model_xml.parent), **options)
    candidates = initial["drawable_candidates"]
    models = [row for row in candidates if row["source"] == "package:" + model_xml.name]
    rigs = [row for row in candidates if row["source"] == "comparison:" + skeleton_xml.name]
    if not models or len(rigs) != 1:
        raise ValueError("Retail report requires explicit model owners and one shared rig owner")
    rig = rigs[0]
    bindings = [{"model": model["source"], "drawable": model["drawable"], "model_sha256": model["sha256"],
                 "rig": rig["source"], "rig_drawable": rig["drawable"], "rig_sha256": rig["sha256"]}
                for model in models if not model["bones"]]
    selected = package_validation.inspect(str(model_xml.parent), rig_bindings=bindings, **options)
    for report in (initial, selected):
        if _bounded(report) != report:
            raise ValueError("Retail report is truncated by the desktop transport")
        if report["runtime_status"] != "not_tested":
            raise ValueError("Offline report incorrectly claims runtime acceptance")
    return initial, selected


def qualify(model_xml: Path, skeleton_xml: Path, edition: str):
    initial, selected = inspect_pair(model_xml, skeleton_xml, edition)
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("Node is required for retail React report qualification")
    desktop = project_root() / "desktop"
    with tempfile.TemporaryDirectory(prefix="allin1-retail-report-") as directory:
        evidence = Path(directory) / "report.json"
        result_file = Path(directory) / "vitest.json"
        evidence.write_text(json.dumps(selected), encoding="utf-8")
        result = subprocess.run([node, str(desktop / "node_modules/vitest/vitest.mjs"), "run", "--config", "vite.config.ts",
                        "src/RetailAssetReport.integration.test.tsx", "--reporter=json", f"--outputFile={result_file}"], cwd=desktop,
                       env={**os.environ, "ALLIN1_RETAIL_ASSET_REPORT": str(evidence)}, timeout=90,
                       capture_output=True, text=True, encoding="utf-8",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode:
            raise RuntimeError("Retail React report failed: " + (result.stderr + result.stdout)[-2000:])
        execution = json.loads(result_file.read_bytes())
        if execution.get("numTotalTests") != 1 or execution.get("numPassedTests") != 1 or execution.get("success") is not True:
            raise ValueError("Retail React report test did not actually execute and pass")
    return {"phase": "asset_validation_report", "source_sha256": selected["source_sha256"],
            "report_sha256": selected["report_sha256"], "shared_rig_count": len(selected["shared_rigs"]),
            "before": {row["category"]: row["status"] for row in initial["checks"]},
            "after": {row["category"]: row["status"] for row in selected["checks"]},
            "findings": [{"category": row["category"], "code": finding["code"], "status": finding["status"],
                          "location": finding["location"], "message": finding["message"]}
                         for row in selected["checks"] for finding in row["findings"]],
            "lod_metrics": selected["lod_metrics"], "react_report": "passed", "runtime_status": "not_tested"}
