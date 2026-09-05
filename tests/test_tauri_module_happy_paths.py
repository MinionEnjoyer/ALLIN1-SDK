import json
from pathlib import Path


def test_every_react_module_has_named_happy_path_evidence():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / "desktop/module-happy-paths.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 1
    modules = manifest["modules"]
    names = [item["module"] for item in modules]
    assert len(names) == len(set(names))
    assert set(names) == {
        "data-tools",
        "xml-editor", "lua-editor",
        "application-shell", "help-center", "package-linker", "asset-viewer",
        "package-receipts", "quick-import", "vehicle-workbench", "weapon-workbench",
        "ped-workbench", "map-workbench", "story-runtime", "render-studio",
        "models-materials", "texture-dictionaries", "rpf-archive-inspection",
        "rpf-archive-utilities", "rpf-game-text", "rpf-binary", "rpf-change-sets",
        "rpf-transactions", "rpf-package-layout", "rpf-build-flow", "package-recipes",
        "sdk-console", "qwen-assistant", "update-check",
    }
    for item in modules:
        source = (root / "desktop" / item["test_file"]).read_text(encoding="utf-8")
        assert item["test_title"] in source, f"Missing happy path: {item['module']}"
    assert sum(bool(item.get("native")) for item in modules) == 4
