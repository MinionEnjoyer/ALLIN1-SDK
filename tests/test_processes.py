from __future__ import annotations

import os
import subprocess
import sys

import pytest

from allin1_sdk import processes


def test_frozen_system_tool_launch_resets_dll_directory_only_for_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frozen CMake child must not inherit PyInstaller's bundled DLL path."""
    calls: list[str | None] = []
    launched: dict[str, object] = {}
    bundle = r"C:\bundle"

    class Process:
        returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def communicate(self, input=None, timeout=None):
            assert calls == [None, bundle]
            assert input is None and timeout == 3
            return "ok", ""

    def popen(command, **kwargs):
        launched["command"] = command
        launched["environment"] = kwargs["env"]
        return Process()

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", bundle, raising=False)
    monkeypatch.setattr(processes, "_set_dll_directory", calls.append)
    monkeypatch.setattr(processes.subprocess, "Popen", popen)

    completed = processes.run_hidden(
        ["cmake.exe", "--version"],
        capture_output=True,
        text=True,
        timeout=3,
        env={"PATH": bundle + os.pathsep + r"C:\Windows\System32"},
        sanitize_frozen_dlls=True,
    )

    assert completed.stdout == "ok"
    assert launched["command"] == ["cmake.exe", "--version"]
    assert launched["environment"] == {"PATH": r"C:\Windows\System32"}


def test_non_system_helper_does_not_change_frozen_dll_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    changed: list[str | None] = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", r"C:\bundle", raising=False)
    monkeypatch.setattr(processes, "_set_dll_directory", changed.append)
    class Process:
        returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def communicate(self, input, timeout=None):
            assert input is None and timeout is None
            return "ok", ""

    monkeypatch.setattr(processes.subprocess, "Popen", lambda *_args, **_kwargs: Process())

    completed = processes.run_hidden(["bundled-helper.exe"], capture_output=True, text=True)

    assert completed.returncode == 0
    assert changed == []


def test_frozen_system_tool_restores_dll_directory_when_spawn_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str | None] = []
    bundle = r"C:\bundle"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", bundle, raising=False)
    monkeypatch.setattr(processes, "_set_dll_directory", calls.append)
    monkeypatch.setattr(
        processes.subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("no cmake")),
    )

    with pytest.raises(OSError, match="no cmake"):
        processes.run_hidden(["cmake.exe"], sanitize_frozen_dlls=True)

    assert calls == [None, bundle]


def test_frozen_system_tool_kills_and_closes_after_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Process:
        returncode = None

        def __enter__(self):
            events.append("enter")
            return self

        def __exit__(self, *_args):
            events.append("exit")
            return False

        def communicate(self, input=None, timeout=None):
            events.append("communicate")
            if len([event for event in events if event == "communicate"]) == 1:
                raise subprocess.TimeoutExpired("cmake.exe", timeout)
            return "", ""

        def kill(self):
            events.append("kill")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", r"C:\bundle", raising=False)
    monkeypatch.setattr(processes, "_set_dll_directory", lambda _directory: None)
    monkeypatch.setattr(processes.subprocess, "Popen", lambda *_args, **_kwargs: Process())

    with pytest.raises(subprocess.TimeoutExpired):
        processes.run_hidden(["cmake.exe"], timeout=1, sanitize_frozen_dlls=True)

    assert events == ["enter", "communicate", "kill", "communicate", "exit"]
