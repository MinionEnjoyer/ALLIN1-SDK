"""Execute the actual NSIS guards without registry, shortcuts, or game access."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def nsis_harness(tmp_path_factory):
    if os.name != "nt":
        pytest.skip("The compiled NSIS guard harness requires Windows")
    compiler = os.environ.get("SDK_NSIS_COMPILER") or shutil.which("makensis")
    if not compiler:
        candidate = Path.home() / "AppData/Local/tauri/NSIS/makensis.exe"
        if candidate.is_file():
            compiler = str(candidate)
    if not compiler:
        pytest.skip("NSIS compiler unavailable; installer guard execution NOT TESTED")
    root = tmp_path_factory.mktemp("nsis guard harness")
    executable = root / "guard.exe"
    uninstaller = root / "guard-uninstall.exe"
    script = root / "guard.nsi"
    guard = ROOT / "desktop/src-tauri/windows/path-guards.nsh"
    script.write_text(f'''
Unicode true
RequestExecutionLevel user
SilentInstall silent
SilentUnInstall silent
OutFile "{executable}"
InstallDir "{root}"
!define SDK_GUARD_DIAGNOSTICS "{root / 'failure.txt'}"
!include "{guard}"
Section
  ${{GetOptions}} $CMDLINE "/GENERATE" $0
  ${{IfNot}} ${{Errors}}
    WriteUninstaller "{uninstaller}"
    SetErrorLevel 0
    Quit
  ${{EndIf}}
  ReadEnvStr $0 SDK_GUARD_TEST_TARGET
  Push $0
  Call SDKGuardTree
  SetOutPath $0
  FileOpen $1 "$0\\written.txt" w
  FileWrite $1 "guard passed"
  FileClose $1
SectionEnd
Section Uninstall
  ReadEnvStr $0 SDK_GUARD_TEST_TARGET
  Push $0
  Call un.SDKGuardTree
  Delete "$0\\remove.txt"
SectionEnd
''', encoding="utf-8")
    result = subprocess.run([compiler, "/V2", str(script)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    subprocess.run([str(executable), "/S", "/GENERATE"], check=True, timeout=20)
    assert uninstaller.is_file()
    return executable, uninstaller


def run_guard(harness, target, uninstall=False):
    executable = harness[bool(uninstall)]
    args = [str(executable), "/S"]
    command = subprocess.list2cmdline(args)
    if uninstall:
        # NSIS consumes the unquoted remainder after _?= (including spaces).
        command += f" _?={executable.parent}"
    return subprocess.run(command, env={**os.environ, "SDK_GUARD_TEST_TARGET": str(target)},
        timeout=20, creationflags=subprocess.CREATE_NO_WINDOW).returncode


@pytest.mark.parametrize("uninstall", [False, True])
def test_nsis_allows_local_spaces_and_preserves_other_data(nsis_harness, tmp_path, uninstall):
    target = tmp_path / "SDK with spaces"
    target.mkdir()
    (target / "remove.txt").write_text("owned")
    (target / "user-data.txt").write_text("preserve")
    assert run_guard(nsis_harness, target, uninstall) == 0, (nsis_harness[0].parent / "failure.txt").read_text()
    assert (target / "user-data.txt").read_text() == "preserve"
    assert (target / "remove.txt").exists() is not uninstall
    assert (target / "written.txt").exists() is not uninstall


def test_nsis_fresh_destination_is_validated_before_creation(nsis_harness, tmp_path):
    target = tmp_path / "fresh" / "SDK with spaces"
    assert run_guard(nsis_harness, target) == 0, (nsis_harness[0].parent / "failure.txt").read_text()
    assert (target / "written.txt").read_text() == "guard passed"


@pytest.mark.parametrize("uninstall", [False, True])
@pytest.mark.parametrize("kind", ["root_junction", "child_junction", "hardlink"])
def test_nsis_link_escape_refused_before_first_write(nsis_harness, tmp_path, uninstall, kind):
    target = tmp_path / "SDK"
    outside = tmp_path / "outside"
    outside.mkdir()
    canary = outside / "remove.txt"
    canary.write_text("untouched")
    if kind == "root_junction":
        link = target
    else:
        target.mkdir()
        (target / "remove.txt").write_text("owned")
        link = target / "redirect"
    if kind == "hardlink":
        os.link(canary, target / "written.txt")
    else:
        # Paths come exclusively from this test's disposable directory.
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
            "New-Item -ItemType Junction -Path $env:SDK_TEST_LINK -Target $env:SDK_TEST_TARGET | Out-Null"],
            env={**os.environ, "SDK_TEST_LINK": str(link), "SDK_TEST_TARGET": str(outside)},
            capture_output=True, text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr
    try:
        assert run_guard(nsis_harness, target, uninstall) == 87
        assert canary.read_text() == "untouched"
        if kind != "root_junction":
            assert (target / "remove.txt").read_text() == "owned"
        if kind != "hardlink":
            assert not (target / "written.txt").exists()
    finally:
        if kind != "hardlink":
            os.rmdir(link)  # Remove only the junction itself, never its target.


@pytest.mark.parametrize("uninstall", [False, True])
@pytest.mark.parametrize("suffix", ["\\..\\escaped", "\\.\\child", " ", ".", "\\\\child", "/child", ":stream", "\\" + "long" * 50])
def test_nsis_aliases_and_unsupported_long_paths_fail_closed(nsis_harness, tmp_path, uninstall, suffix):
    target = tmp_path / "SDK"
    target.mkdir()
    (target / "remove.txt").write_text("owned")
    assert run_guard(nsis_harness, str(target) + suffix, uninstall) == 87
    assert (target / "remove.txt").read_text() == "owned"
    assert not (tmp_path / "escaped").exists()
    assert not (target / "written.txt").exists()


def test_template_guards_precede_mutations_and_never_execute_registry_command():
    template = (ROOT / "desktop/src-tauri/windows/installer.nsi").read_text()
    assert 'Section Install\n  !insertmacro SDK_GUARD_INSTALL ""\n  SetOutPath' in template
    assert 'Section EarlyChecks\n  !insertmacro SDK_GUARD_INSTALL ""' in template
    assert 'Section Uninstall\n  !insertmacro SDK_GUARD_INSTALL "un."' in template
    assert 'Delete "$INSTDIR\\$OldMainBinaryName"' not in template
    assert 'StrCpy $R1 \'"$4\\uninstall.exe"\'' in template
    assert '$TEMP\\MicrosoftEdge' not in template
