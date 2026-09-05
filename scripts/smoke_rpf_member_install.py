"""Install an exported schema-3/4 text fixture in a temporary game, never the real game."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def verify_export(package_path, original_archive, patcher, game_context, launcher_source, unrelated_original):
    sys.path.insert(0, str(Path(launcher_source).resolve(strict=True)))
    from allin1.mods import ModIntegrationService, open_mod_package
    original_archive = Path(original_archive).resolve(strict=True)
    original_hash = hashlib.sha256(original_archive.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="allin1-member-install-") as directory, open_mod_package(package_path) as manifest:
        root = Path(directory).resolve()
        game = root / "game"; game.mkdir()
        (game / "GTA5_Enhanced.exe").write_bytes(b"temporary non-executable marker")
        assert manifest.schema_version in (3, 4) and not manifest.files and len(manifest.rpf_entries) == 1
        patch = manifest.rpf_entries[0]
        nested_patch = manifest.schema_version == 4
        assert patch.entry.as_posix() == ("x64/american.rpf!global.gxt2" if nested_patch else "global.gxt2")
        target = game.joinpath(*patch.archive.parts)
        assert target.resolve().is_relative_to(game) and target.name == original_archive.name
        target.parent.mkdir(parents=True)
        shutil.copy2(original_archive, target)
        payload = manifest.package_root.joinpath(*patch.source.parts).read_bytes()
        probe = root / "probe.gxt2"

        def run(command, archive, *args):
            assert Path(archive).resolve().is_relative_to(game), "Refusing non-test archive"
            return subprocess.run([str(patcher), command, str(game_context), str(archive), *map(str, args)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

        def read(unrelated=False):
            probe.unlink(missing_ok=True)
            nested = not nested_patch if unrelated else nested_patch
            entry = "x64/american.rpf!global.gxt2" if nested else "global.gxt2"
            result = run("extract-exact-nested-entry" if nested else "extract-exact-entry", target, entry, probe)
            assert result.returncode == 0, result.stderr
            return probe.read_bytes()

        original = read()
        assert hashlib.sha256(original).hexdigest() == patch.original_sha256
        assert read(True) == unrelated_original
        service = ModIntegrationService(game)
        service._check_dependencies = lambda value: None  # No installed loader in the temporary fake game.
        service._run_rpf_command = run
        service.install(manifest)
        assert read() == payload and read(True) == unrelated_original
        assert service._read_receipt(manifest.mod_id)["schema_version"] == manifest.schema_version
        service.set_enabled(manifest.mod_id, False)
        assert read() == original and read(True) == unrelated_original
        service.set_enabled(manifest.mod_id, True)
        assert read() == payload and read(True) == unrelated_original
        service.uninstall(manifest.mod_id)
        assert read() == original and read(True) == unrelated_original
        assert not service._receipt_path(manifest.mod_id).exists()
        # A different existing dictionary is rejected before modifying it.
        wrong = root / "wrong.gxt2"; wrong.write_bytes(payload)
        replaced = (run("replace-exact-nested-entry", target, patch.entry, wrong, patch.original_sha256, patch.sha256)
                    if nested_patch else run("replace-entry", target, patch.entry, wrong))
        assert replaced.returncode == 0, replaced.stderr
        before = target.read_bytes()
        try:
            service.install(manifest)
        except ValueError as error:
            assert "Original RPF member checksum mismatch" in str(error)
        else:
            raise AssertionError("Exact-member install accepted a mismatched original")
        assert target.read_bytes() == before and read(True) == unrelated_original
        assert not service._receipt_path(manifest.mod_id).exists()
    assert hashlib.sha256(original_archive.read_bytes()).hexdigest() == original_hash
    print("Exported exact-member ZIP: native install / disable / enable / uninstall and original-checksum refusal passed; real GTA untouched.", flush=True)
