from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from allin1_sdk import launcher_bridge
from allin1_sdk.agent_api import command_catalog
from allin1_sdk.cli import main


def test_launcher_bridge_prefers_explicit_executable_and_uses_typed_argv(
    tmp_path: Path,
):
    executable = tmp_path / "ALLIN1-Launcher.exe"
    executable.write_bytes(b"fixture")

    command, cwd = launcher_bridge.launcher_process_command(
        tmp_path,
        "Studio.Pagani",
        traffic_requested=True,
        executable=executable,
    )

    assert command == [
        str(executable.resolve()),
        "--workspace", "packages",
        "--package-id", "studio.pagani",
        "--traffic", "on",
    ]
    assert cwd is None


def test_launcher_bridge_uses_sibling_source_checkout_without_shell(
    tmp_path: Path, monkeypatch,
):
    sdk = tmp_path / "ALLIN1-SDK"
    sdk.mkdir()
    core = tmp_path / "ALLIN1"
    (core / "src" / "allin1").mkdir(parents=True)
    (core / "src" / "allin1" / "gui.py").write_text("", encoding="utf-8")
    python = core / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    monkeypatch.setattr(launcher_bridge.shutil, "which", lambda _name: None)

    command, cwd = launcher_bridge.launcher_process_command(
        sdk, "studio.pagani", traffic_requested=False, environment={},
    )

    assert command[:3] == [str(python), "-m", "allin1.gui"]
    assert command[-2:] == ["--traffic", "off"]
    assert cwd == core


def test_launcher_bridge_fails_closed_and_popen_receives_argv(
    tmp_path: Path, monkeypatch,
):
    with pytest.raises(ValueError, match="valid package ID"):
        launcher_bridge.launcher_process_command(
            tmp_path, "../unsafe", traffic_requested=False,
        )

    executable = tmp_path / "launcher.exe"
    executable.write_bytes(b"")
    calls: list[tuple[list[str], dict[str, object]]] = []

    class _Process:
        pass

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return _Process()

    monkeypatch.setattr(launcher_bridge.subprocess, "Popen", popen)
    result = launcher_bridge.open_launcher_packages(
        tmp_path, "studio.pagani", traffic_requested=False,
        executable=executable,
    )

    assert isinstance(result, _Process)
    assert calls[0][0][-2:] == ["--traffic", "off"]
    assert calls[0][1] == {"cwd": None, "close_fds": True}
    assert "shell" not in calls[0][1]


def test_launcher_bridge_matches_manifest_package_id_bounds(tmp_path: Path):
    executable = tmp_path / "launcher.exe"
    executable.write_bytes(b"")
    valid = "a" * 64
    command, _cwd = launcher_bridge.launcher_process_command(
        tmp_path, valid, traffic_requested=None, executable=executable,
    )
    assert command[-2:] == ["--package-id", valid]
    with pytest.raises(ValueError, match="valid package ID"):
        launcher_bridge.launcher_process_command(
            tmp_path, "a" * 65, traffic_requested=None, executable=executable,
        )
    with pytest.raises(ValueError, match="valid package ID"):
        launcher_bridge.launcher_process_command(
            tmp_path, None, traffic_requested=None, executable=executable,  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="traffic intent"):
        launcher_bridge.launcher_process_command(
            tmp_path, valid, traffic_requested=1, executable=executable,  # type: ignore[arg-type]
        )


def test_cli_opens_launcher_without_install_authority(monkeypatch):
    launched: dict[str, object] = {}

    def fake_open(package_id, *, traffic=None):
        launched.update({
            "package_id": package_id, "traffic": traffic,
        })
        return SimpleNamespace(pid=4321)

    monkeypatch.setattr("allin1_sdk.cli.open_launcher_package", fake_open)
    result = CliRunner().invoke(main, [
        "open-launcher-package", "studio.pagani", "--traffic",
    ])

    assert result.exit_code == 0, result.output
    assert '"install_performed": false' in result.output
    assert launched == {
        "package_id": "studio.pagani",
        "traffic": True,
    }


def test_launcher_navigation_has_explicit_read_only_agent_risk():
    catalog = {item["name"]: item for item in command_catalog()}
    assert catalog["open-launcher-package"]["risk"] == "read_only"
    parameters = {
        item["name"] for item in catalog["open-launcher-package"]["parameters"]
    }
    assert "launcher_path" not in parameters


def test_sdk_host_wires_quick_import_launcher_callback():
    shell = (
        Path(__file__).parents[1] / "src" / "allin1_sdk" / "addon_sdk_ui.py"
    ).read_text(encoding="utf-8")

    assert "on_open_launcher=self._open_quick_import_launcher" in shell
    assert "def _open_quick_import_launcher(" in shell
