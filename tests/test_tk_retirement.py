"""Prevent retired GUI source and broken entrypoints from returning."""
from pathlib import Path
from unittest.mock import Mock
import subprocess
import sys

import pytest

from allin1_sdk import desktop_entry as entry
from scripts.tk_retirement import PACKAGE, audit

ROOT = Path(__file__).resolve().parents[1]


def test_product_source_is_tk_free_and_gui_alias_is_native():
    result = audit(ROOT)
    assert result["status"] == "PASS", result
    assert result["python_sources"] > 50
    assert result["release_ready"] is False


@pytest.mark.parametrize("source", [
    "import tkinter", "from tkinter import ttk", "import _tkinter",
    "from PIL import ImageTk", "from PIL import _imagingtk",
])
def test_source_gate_rejects_reintroduced_tk(tmp_path, source):
    folder = tmp_path / "src" / PACKAGE
    folder.mkdir(parents=True)
    (folder / "new_panel.py").write_text(source)
    (tmp_path / "pyproject.toml").write_bytes((ROOT / "pyproject.toml").read_bytes())
    assert audit(tmp_path)["status"] == "FAIL"


@pytest.fixture
def isolated_discovery(monkeypatch):
    monkeypatch.delenv(entry.EXECUTABLE_ENV, raising=False)
    monkeypatch.setattr(entry.sys, "frozen", False, raising=False)
    monkeypatch.setattr(entry.shutil, "which", lambda _: None)


def test_missing_native_desktop_never_falls_back_to_python(isolated_discovery, monkeypatch, capsys):
    launched = Mock()
    monkeypatch.setattr(entry.subprocess, "Popen", launched)
    assert entry.main([]) == 1
    assert "Python-only" in capsys.readouterr().err
    launched.assert_not_called()


def test_gui_alias_forwards_paths_as_argv_without_console(isolated_discovery, tmp_path, monkeypatch):
    native = tmp_path / "App with spaces.exe"
    native.write_bytes(b"inert test fixture; never executed")
    monkeypatch.setenv(entry.EXECUTABLE_ENV, str(native))
    launched = Mock()
    monkeypatch.setattr(entry.subprocess, "Popen", launched)
    args = ["--workspace", "help", "--addon-manifest", "relative path/a & b.json"]
    assert entry.main(args) == 0
    launched.assert_called_once_with([str(native.resolve()), *args], close_fds=True,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def test_frozen_companion_resolves_native_shell_not_sidecar(isolated_discovery, tmp_path, monkeypatch):
    service = tmp_path / "sidecar" / "service.exe"
    service.parent.mkdir()
    service.write_bytes(b"not executed")
    native = tmp_path / entry.EXECUTABLE_NAME
    native.write_bytes(b"not executed")
    monkeypatch.setattr(entry.sys, "frozen", True)
    assert entry.desktop_executable(service) == native.resolve()


def test_bad_explicit_path_does_not_fall_back_to_path(isolated_discovery, tmp_path, monkeypatch):
    monkeypatch.setenv(entry.EXECUTABLE_ENV, str(tmp_path / "missing.exe"))
    with pytest.raises(FileNotFoundError):
        entry.desktop_executable()
    monkeypatch.setenv(entry.EXECUTABLE_ENV, str(tmp_path))
    with pytest.raises(FileNotFoundError, match="not a file"):
        entry.desktop_executable()


def test_path_discovery_uses_only_native_name(isolated_discovery, tmp_path, monkeypatch):
    native = tmp_path / entry.EXECUTABLE_NAME
    native.write_bytes(b"not executed")
    lookup = Mock(return_value=str(native))
    monkeypatch.setattr(entry.shutil, "which", lookup)
    assert entry.desktop_executable() == native.resolve()
    lookup.assert_called_once_with(entry.EXECUTABLE_NAME)
