"""Exercise real native member identity on owned temporary RPFs only.

The game directory is a read-only key/edition context. Optional Launcher checks
install, disable, enable and uninstall solely in a generated temporary game.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile


def dictionary(value):
    text = value.encode("utf-8") + b"\0"
    return b"2TXG" + struct.pack("<III", 1, 256, 24) + b"2TXG" + struct.pack("<I", 24 + len(text)) + text


def smoke(patcher: Path, game_context: Path, *, batch=False, launcher_source=None):
    with tempfile.TemporaryDirectory(prefix="allin1-exact-rpf-smoke-") as temporary:
        root = Path(temporary).resolve()
        loose, nested = root / "loose", root / "nested"
        loose.mkdir(); nested.mkdir()
        originals = {
            "global.gxt2": dictionary("Root"),
            "text/global.gxt2": dictionary("Text"),
            "shadow/text/global.gxt2": dictionary("Suffix decoy"),
            "shadow/only/global.gxt2": dictionary("Missing directory decoy"),
            "shadow/new.gxt2": dictionary("Missing root decoy"),
        }
        for name, content in originals.items():
            member = loose / name
            member.parent.mkdir(parents=True, exist_ok=True)
            member.write_bytes(content)
        (nested / "global.gxt2").write_bytes(dictionary("Nested"))
        archive, probe = root / "fixture.rpf", root / "probe.gxt2"
        calls = 0

        def command(*args, expected=0):
            nonlocal calls
            result = subprocess.run([str(patcher), *map(str, args)], cwd=root,
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            calls += 1
            expected_codes = (expected,) if isinstance(expected, int) else expected
            if result.returncode not in expected_codes:
                raise RuntimeError(f"Native {args[0]} returned {result.returncode}, expected {expected}: {result.stderr}")
            return result

        def extract(member, *, selected=archive, nested_path=None, expected=0):
            probe.unlink(missing_ok=True)
            if nested_path is None:
                result = command("extract-exact-entry", game_context, selected, member, probe, expected=expected)
            else:
                result = command("extract-virtual-entry", game_context, selected, nested_path, member, probe, expected=expected)
            if expected:
                assert not probe.exists(), "Failed read published an output"
                return result
            return probe.read_bytes()

        def check(selected=archive, changed=None, absent=()):
            for member, content in {**originals, **(changed or {})}.items():
                if member not in absent:
                    assert extract(member, selected=selected) == content, f"Wrong member: {member}"
            assert extract("global.gxt2", selected=selected, nested_path="x64/american.rpf") == dictionary("Nested")

        command("build-dlc", loose, archive, "--embed-rpf", nested, "x64/american.rpf")
        initial = archive.read_bytes()
        pristine = root / "pristine" / "fixture.rpf"
        pristine.parent.mkdir()
        pristine.write_bytes(initial)
        check()
        assert extract("TEXT\\GLOBAL.GXT2") == originals["text/global.gxt2"]
        for missing in ("only/global.gxt2", "new.gxt2", "american.rpf/global.gxt2"):
            assert "Entry not found:" in extract(missing, expected=5).stderr
        for unsafe in ("../global.gxt2", "text/../global.gxt2", "/global.gxt2", "text//global.gxt2", "x64/american.rpf!global.gxt2"):
            assert "Unsafe exact RPF entry path" in extract(unsafe, expected=99).stderr
        assert archive.read_bytes() == initial
        print("Exact reads: root/folder/nested identities and missing/unsafe paths passed", flush=True)

        replacement = root / "replacement.gxt2"
        replacement.write_bytes(dictionary("Edited"))
        command("replace-entry", game_context, archive, "text/global.gxt2", replacement)
        check(changed={"text/global.gxt2": replacement.read_bytes()})
        before = archive.read_bytes()
        command("delete-entry", game_context, archive, "only/global.gxt2")
        assert archive.read_bytes() == before
        command("replace-entry", game_context, archive, "only/added.gxt2", replacement, expected=5)
        assert archive.read_bytes() == before
        command("replace-entry", game_context, archive, "new.gxt2", replacement)
        assert extract("new.gxt2") == replacement.read_bytes()
        command("delete-entry", game_context, archive, "text/global.gxt2")
        extract("text/global.gxt2", expected=5)
        check(absent=("text/global.gxt2",))
        print("Exact writes: replacement/add/delete preserve suffix decoys and nested payloads", flush=True)

        if batch:
            staged = root / "batch" / "fixture.rpf"
            staged.parent.mkdir()
            shutil.copy2(pristine, staged)
            plan = root / "changes.tsv"
            plan.write_text("replace\ttext/global.gxt2\treplacement.gxt2\n", encoding="utf-8")
            command("apply-entry-changes", game_context, staged, plan, root)
            check(staged, {"text/global.gxt2": replacement.read_bytes()})
            before = staged.read_bytes()
            plan.write_text("replace\tonly/global.gxt2\treplacement.gxt2\n", encoding="utf-8")
            command("apply-entry-changes", game_context, staged, plan, root, expected=99)
            assert staged.read_bytes() == before
            print("SDK batch: exact replacement and absent-target refusal passed", flush=True)

        if launcher_source:
            sys.path.insert(0, str(launcher_source))
            from allin1.mods import ModIntegrationService, ModManifest
            fake_game = root / "game"
            fake_game.mkdir()
            (fake_game / "GTA5_Enhanced.exe").write_bytes(b"temporary marker, not executable")
            target = fake_game / "mods" / "fixture.rpf"
            target.parent.mkdir()
            shutil.copy2(pristine, target)
            package = root / "package"
            package.mkdir()
            shutil.copy2(replacement, package / "replacement.gxt2")
            (package / "mod.toml").write_text(
                'schema_version = 1\nid = "smoke.exact-text"\nname = "Exact text smoke"\nversion = "1.0.0"\n'
                'type = "rpf"\neditions = ["enhanced"]\ndependencies = ["openrpf"]\n'
                '[[rpf_entries]]\nsource = "replacement.gxt2"\narchive = "mods/fixture.rpf"\n'
                'entry = "text/global.gxt2"\nsha256 = "' + hashlib.sha256(replacement.read_bytes()).hexdigest() + '"\n', encoding="utf-8")
            service = ModIntegrationService(fake_game)
            service._check_dependencies = lambda manifest: None  # No real loader in a fake game.

            def managed_command(verb, *args):
                assert Path(args[0]).resolve().is_relative_to(fake_game), "Refusing non-test archive mutation"
                # The real helper gets real keys, but only a temporary archive.
                return command(verb, game_context, *args, expected=(0, 5) if verb == "extract-exact-entry" else 0)

            service._run_rpf_command = managed_command
            manifest = ModManifest.load(package)
            service.install(manifest)
            check(target, {"text/global.gxt2": replacement.read_bytes()})
            receipt = json.loads(service._receipt_path(manifest.mod_id).read_text())
            assert len(receipt["rpf_entries"]) == 1 and not receipt["files"]
            service.set_enabled(manifest.mod_id, False)
            check(target)
            service.set_enabled(manifest.mod_id, True)
            check(target, {"text/global.gxt2": replacement.read_bytes()})
            service.uninstall(manifest.mod_id)
            check(target)
            assert not service._receipt_path(manifest.mod_id).exists()
            print("Launcher: native member install / disable / enable / uninstall passed in temporary game", flush=True)
            # A missing root member must be added and removed, never backed up
            # from or written into the existing shadow/new.gxt2 suffix decoy.
            manifest_path = package / "mod.toml"
            manifest_path.write_text(manifest_path.read_text(encoding="utf-8")
                .replace('"smoke.exact-text"', '"smoke.exact-root"')
                .replace('entry = "text/global.gxt2"', 'entry = "new.gxt2"'), encoding="utf-8")
            manifest = ModManifest.load(package)
            service.install(manifest)
            check(target, {"new.gxt2": replacement.read_bytes()})
            receipt = json.loads(service._receipt_path(manifest.mod_id).read_text())
            assert receipt["rpf_entries"][0]["backup"] is None
            service.set_enabled(manifest.mod_id, False)
            extract("new.gxt2", selected=target, expected=5)
            check(target)
            service.set_enabled(manifest.mod_id, True)
            check(target, {"new.gxt2": replacement.read_bytes()})
            service.uninstall(manifest.mod_id)
            extract("new.gxt2", selected=target, expected=5)
            check(target)
            assert not service._receipt_path(manifest.mod_id).exists()
            print("Launcher: missing-root lifecycle preserved shadow/new.gxt2 without a false backup", flush=True)
        assert pristine.read_bytes() == initial
        print(f"Exact-member native smoke passed ({calls} helper calls; no real game writes)", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patcher", type=Path, required=True)
    parser.add_argument("--game-context", type=Path, required=True)
    parser.add_argument("--batch", action="store_true", help="Also test SDK-only batch mutation")
    parser.add_argument("--launcher-source", type=Path, help="Optional Launcher src directory for temporary install lifecycle")
    options = parser.parse_args()
    smoke(options.patcher.resolve(strict=True), options.game_context.resolve(strict=True), batch=options.batch,
          launcher_source=options.launcher_source.resolve(strict=True) if options.launcher_source else None)


if __name__ == "__main__":
    main()
