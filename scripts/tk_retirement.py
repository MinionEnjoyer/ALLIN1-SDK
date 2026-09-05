"""Read-only source retirement gate. Not packaged/live release qualification."""
import ast
import json
from pathlib import Path
try:
    import tomllib
except ImportError:
    import tomli as tomllib

PACKAGE = "allin1_sdk"
RETIRED_MODULES = ["addon_sdk_ui","app","asset_viewer","binary_workspace_ui","branding","collapsible_panes","compiled_render_ui","help_center","map_workbench","model_material_workbench","oiv_workbench_ui","ped_workbench","quick_import_ui","rpf_change_set_ui","rpf_explorer","rpf_graph_ui","rpf_program_ui","sdk_console","texture_editor","ui_foundation","update_ui","vehicle_axles_ui","vehicle_oiv_ui","vehicle_workbench","weapon_workbench","workbench"]
FORBIDDEN_IMPORTS = {"tkinter", "_tkinter", "PIL.ImageTk", "PIL._imagingtk"}


def audit(root: Path) -> dict:
    problems = []
    files = sorted((root / "src" / PACKAGE).rglob("*.py"))
    if not files:
        problems.append("Product source is missing")
    retired = {PACKAGE + "." + name for name in RETIRED_MODULES}
    for name in RETIRED_MODULES:
        if (root / "src" / PACKAGE / (name + ".py")).exists():
            problems.append("Retired adapter remains: " + name)
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level:
                    module = PACKAGE + ("." + module if module else "")
                imports.append(module)
                imports.extend(module + "." + alias.name for alias in node.names)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in retired:
                problems.append(f"{path.relative_to(root)}: retired dynamic import/launch target {node.value}")
        for name in imports:
            if name in retired or any(name == value or name.startswith(value + ".") for value in FORBIDDEN_IMPORTS):
                problems.append(f"{path.relative_to(root)}: forbidden import {name}")
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if project.get("project", {}).get("gui-scripts", {}).get("allin1-sdk-gui") != PACKAGE + ".desktop_entry:main":
        problems.append("GUI alias must target the native desktop entry wrapper")
    for path in (root / ".github/workflows").glob("*.yml"):
        text = path.read_text(encoding="utf-8")
        for name in RETIRED_MODULES:
            if f"src/{PACKAGE}/{name}.py" in text or f"{PACKAGE}.{name}:main" in text:
                problems.append(f"{path.relative_to(root)}: retired GUI build target {name}")
    return {"status": "FAIL" if problems else "PASS", "scope": "source and entrypoints only",
            "python_sources": len(files), "retired_adapters": len(RETIRED_MODULES),
            "problems": problems, "release_ready": False}


if __name__ == "__main__":
    result = audit(Path(__file__).resolve().parents[1])
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
