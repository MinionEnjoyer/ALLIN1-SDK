"""Compiled-asset evidence is distinct from native-window acceptance."""
import pytest
from scripts.desktop_candidate import verify_frontend_probe

@pytest.mark.parametrize("mutation", [None, "dev", "other-build", "other-version", "boolean-schema", "empty", "missing", "duplicate", "zero-bytes", "boolean-bytes", "overclaimed"])
def test_compiled_frontend_evidence_requires_exact_assets(tmp_path, mutation):
    frontend = tmp_path / "frontend"; frontend.mkdir()
    for name in ["index.html", "app.js", "app.css"]:
        (frontend / name).write_bytes(b"fixture frontend")
    identity = {"build_id": "a" * 32, "sdk_version": "0.6.4"}
    report = {"schema_version": 1, "kind": "embedded_frontend_probe", "status": "PASS",
              "production": True, "build_id": identity["build_id"], "version": "0.6.4",
              "native_ui": "NOT TESTED", "release_ready": False,
              "assets": [{"path": name, "bytes": 16} for name in ["index.html", "app.js", "app.css"]]}
    if mutation == "dev": report["production"] = False
    elif mutation == "other-build": report["build_id"] = "b" * 32
    elif mutation == "other-version": report["version"] = "0.6.3"
    elif mutation == "boolean-schema": report["schema_version"] = True
    elif mutation == "empty": report["assets"] = []
    elif mutation == "missing": report["assets"].pop()
    elif mutation == "duplicate": report["assets"].append(dict(report["assets"][0]))
    elif mutation == "zero-bytes": report["assets"][0]["bytes"] = 0
    elif mutation == "boolean-bytes": report["assets"][0]["bytes"] = True
    elif mutation == "overclaimed": report["native_ui"] = "PASS"
    if mutation:
        with pytest.raises(ValueError):
            verify_frontend_probe(report, identity, frontend)
    else:
        verify_frontend_probe(report, identity, frontend)
