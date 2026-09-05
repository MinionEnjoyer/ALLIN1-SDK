"""SDK/Launcher must not use basename lookup for managed RPF members."""
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest


@pytest.fixture(params=["sdk", "launcher"])
def service(request, monkeypatch, tmp_path):
    if request.param == "sdk":
        from allin1_sdk.mods import ModIntegrationService
    else:
        launcher = Path(__file__).resolve().parents[2] / "ALLIN1" / "src"
        if not launcher.is_dir():
            pytest.skip("Sibling Launcher checkout is unavailable")
        monkeypatch.syspath_prepend(str(launcher))
        from allin1.mods import ModIntegrationService
    game = tmp_path / "game"
    game.mkdir()
    (game / "GTA5_Enhanced.exe").write_bytes(b"test marker only")
    return ModIntegrationService(game)


@pytest.mark.parametrize("entry", ["global.gxt2", "text/global.gxt2"])
def test_managed_reads_use_exact_member_command(service, monkeypatch, tmp_path, entry):
    output = tmp_path / "probe.gxt2"
    output.write_bytes(b"stale probe")
    archive = tmp_path / "fixture.rpf"
    calls = []

    def run(command, *arguments):
        calls.append((command, *arguments))
        assert not output.exists()
        output.write_bytes(b"exact member")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(service, "_run_rpf_command", run)
    assert service._extract_rpf_entry(archive, PurePosixPath(entry), output)
    assert calls == [("extract-exact-entry", archive, entry, output)]
    assert output.read_bytes() == b"exact member"


@pytest.mark.parametrize("code,detail", [
    (1, "Unknown command: extract-exact-entry"),
    (99, "RPF extraction failed"),
    (5, "RPF entry path is ambiguous: global.gxt2"),
])
def test_old_or_failed_helper_never_falls_back(service, monkeypatch, tmp_path, code, detail):
    calls = []

    def run(command, *arguments):
        calls.append(command)
        return SimpleNamespace(returncode=code, stdout="", stderr=detail)

    monkeypatch.setattr(service, "_run_rpf_command", run)
    with pytest.raises(RuntimeError, match="Could not extract RPF entry"):
        service._extract_rpf_entry(tmp_path / "fixture.rpf", "global.gxt2", tmp_path / "out", allow_missing=True)
    assert calls == ["extract-exact-entry"]


def test_exact_missing_is_distinct_from_failed_extraction(service, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_run_rpf_command", lambda *args: SimpleNamespace(
        returncode=5, stdout="", stderr="ERROR: Entry not found: global.gxt2"))
    assert not service._extract_rpf_entry(tmp_path / "fixture.rpf", "global.gxt2", tmp_path / "out", allow_missing=True)
    with pytest.raises(RuntimeError):
        service._extract_rpf_entry(tmp_path / "fixture.rpf", "global.gxt2", tmp_path / "out")


def test_success_requires_an_extracted_file(service, monkeypatch, tmp_path):
    monkeypatch.setattr(service, "_run_rpf_command", lambda *args: SimpleNamespace(returncode=0, stdout="", stderr=""))
    with pytest.raises(RuntimeError, match="without extracting"):
        service._extract_rpf_entry(tmp_path / "fixture.rpf", "global.gxt2", tmp_path / "out")


def test_sdk_and_launcher_share_exact_native_resolution():
    root = Path(__file__).resolve().parents[1]
    launcher = root.parent / "ALLIN1" / "tools" / "RpfPatcher" / "Program.cs"
    if not launcher.is_file():
        pytest.skip("Sibling Launcher checkout is unavailable")

    def methods(path):
        text = path.read_text(encoding="utf-8")
        return text[text.index("        static RpfFileEntry FindExactFileEntry("):
                    text.index("        static RpfFile OpenWritableRpf(")]

    assert methods(root / "tools" / "RpfPatcher" / "Program.cs") == methods(launcher)
