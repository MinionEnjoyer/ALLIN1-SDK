"""Package policy checks never execute an installed SDK or touch game data."""
import ast
import io
from pathlib import Path
import zipfile

import pytest

from scripts.frozen_desktop import EXCLUDED_MODULES, assert_no_tk, inspect_frozen


@pytest.mark.parametrize("name", [
    "tkinter", "tkinter.ttk", "tkinter/ttk.pyc", "_tkinter.pyd", "PIL.ImageTk",
    "PIL/_imagingtk.pyd", "_tcl_data/init.tcl", "_tk_data/ttk/ttk.tcl",
    "tcl86t.dll", "TK86T.DLL", "tcl8/encoding.enc", "pyi_rth__tkinter",
    "allin1_sdk.help_center", "allin1_sdk/quick_import_ui.pyc",
])
def test_frozen_payload_rejects_legacy_modules_and_runtime_data(name):
    with pytest.raises(ValueError, match="forbidden"):
        assert_no_tk(["allin1_sdk.desktop_protocol", name])


def test_removed_adapters_stay_excluded_and_source_contains_no_tk():
    from scripts.tk_retirement import RETIRED_MODULES, audit
    root = Path(__file__).resolve().parents[1]
    assert audit(root)["status"] == "PASS"
    assert {"allin1_sdk." + name for name in RETIRED_MODULES} <= set(EXCLUDED_MODULES)
    for source in (root / "src/allin1_sdk").glob("*.py"):
        imports = []
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Import):
                imports.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append((node.module or "").split(".")[0])
        assert not {"tkinter", "_tkinter"}.intersection(imports), source.name


@pytest.mark.parametrize("where", ["carchive", "pyz", "embedded_zip", "loose_zip", "loose_file", "clean"])
def test_frozen_inspection_covers_all_packaging_layers(tmp_path, monkeypatch, where):
    from PyInstaller.archive import readers
    sidecar = tmp_path / "sidecar.exe"
    sidecar.write_bytes(b"fixture; never executed")
    forbidden = "_tk_data/tk.tcl"
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(forbidden if where == "embedded_zip" else "encodings/utf_8.pyc", b"data")

    class Archive:
        toc = {"PYZ.pyz": ("z",), "base_library.zip": ("x",)}
        if where == "carchive":
            toc["tcl86t.dll"] = ("b",)

        def __init__(self, _path):
            pass

        def open_embedded_archive(self, _name):
            return type("Pyz", (), {"toc": {"tkinter" if where == "pyz" else "allin1_sdk.desktop_protocol": ()}})()

        def extract(self, _name):
            return data.getvalue()

    monkeypatch.setattr(readers, "CArchiveReader", Archive)
    if where == "loose_file":
        (tmp_path / "tk86t.dll").write_bytes(b"data")
    if where == "loose_zip":
        with zipfile.ZipFile(tmp_path / "stdlib.zip", "w") as archive:
            archive.writestr(forbidden, b"data")
    if where == "clean":
        assert inspect_frozen(sidecar)["status"] == "PASS"
    else:
        with pytest.raises(ValueError, match="forbidden"):
            inspect_frozen(sidecar)


def test_frozen_inspection_does_not_accept_an_executable_header_alone(tmp_path):
    executable = tmp_path / "not-a-frozen-service.exe"
    executable.write_bytes(b"MZ" + b"\0" * 100)
    with pytest.raises(Exception):
        inspect_frozen(executable)
