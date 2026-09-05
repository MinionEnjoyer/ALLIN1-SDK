"""Enforce the Tk-free desktop payload boundary, independently of UI tests."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

from allin1_sdk.release_identity import sha256
from allin1_sdk.release_paths import no_links, tree_files


LEGACY_MODULES = tuple("allin1_sdk." + name for name in (
    "app", "addon_sdk_ui", "asset_viewer", "binary_workspace_ui", "branding",
    "collapsible_panes", "compiled_render_ui", "help_center", "map_workbench", "model_material_workbench",
    "oiv_workbench_ui", "ped_workbench", "quick_import_ui", "rpf_change_set_ui",
    "rpf_explorer", "rpf_graph_ui", "rpf_program_ui", "sdk_console", "texture_editor",
    "ui_foundation", "update_ui", "vehicle_axles_ui", "vehicle_oiv_ui",
    "vehicle_workbench", "weapon_workbench", "workbench",
))
EXCLUDED_MODULES = ("tkinter", "_tkinter", "PIL.ImageTk", "PIL._imagingtk", *LEGACY_MODULES)


def assert_no_tk(names) -> None:
    for name in names:
        normalized = str(name).replace("\\", "/").casefold()
        module = normalized.replace("/", ".")
        if (any(re.match(r"^(?:_?tkinter(?:\.|$)|_t[ck]l?_data$|tcl\d|tk\d|pyi_rth__tkinter(?:\.|$))", part)
                for part in normalized.split("/"))
                or any(module == banned.casefold() or module.startswith(banned.casefold() + ".")
                       for banned in EXCLUDED_MODULES)):
            raise ValueError("Tk/legacy UI is forbidden in the React SDK payload: " + str(name))


def inspect_frozen(sidecar: Path) -> dict:
    """Inspect loose files, the executable CArchive, PYZ modules and ZIPs.

    No executable is started. A valid PE header is not sufficient evidence.
    """
    from PyInstaller.archive.readers import CArchiveReader

    sidecar = no_links(sidecar).resolve(strict=True)
    before = sha256(sidecar)
    files = tree_files(sidecar.parent)
    names = list(files)
    archive = CArchiveReader(str(sidecar))
    names.extend(archive.toc)
    for name, entry in archive.toc.items():
        if entry[-1] == "z":
            names.extend(archive.open_embedded_archive(name).toc)
        elif name.casefold().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(archive.extract(name))) as zipped:
                names.extend(zipped.namelist())
    for path in files.values():
        if path.suffix.casefold() == ".zip":
            with zipfile.ZipFile(path) as zipped:
                names.extend(zipped.namelist())
    assert_no_tk(names)
    if sha256(sidecar) != before:
        raise ValueError("Sidecar changed during its payload inspection")
    return {"schema_version": 1, "status": "PASS", "sidecar_sha256": before,
            "files_and_modules_inspected": len(names),
            "inventory_sha256": hashlib.sha256(json.dumps(sorted(names)).encode()).hexdigest(),
            "scope": "frozen payload inventory; not runtime or live acceptance"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("exclusions", "inspect"))
    parser.add_argument("sidecar", type=Path, nargs="?")
    args = parser.parse_args()
    if args.action == "exclusions":
        print("\n".join(EXCLUDED_MODULES))
    else:
        if args.sidecar is None:
            parser.error("inspect requires a frozen sidecar")
        print(json.dumps(inspect_frozen(args.sidecar), sort_keys=True))


if __name__ == "__main__":
    main()
