"""Bounded native two-layer fixture smoke. Real GTA supplies decoding keys only."""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--patcher", type=Path, required=True)
    parser.add_argument("--game-context", type=Path, required=True)
    options = parser.parse_args()
    sha = lambda data: hashlib.sha256(data).hexdigest()
    with tempfile.TemporaryDirectory(prefix="allin1-nested-native-") as directory:
        root = Path(directory).resolve()
        deep, middle, outer = root / "deep", root / "middle", root / "outer"
        for path in (deep, middle, outer): path.mkdir()
        for path, text in ((deep, b"deep original"), (middle, b"middle decoy"), (outer, b"root decoy")):
            (path / "global.gxt2").write_bytes(text)
        (deep / "other.gxt2").write_bytes(b"unrelated original")

        def build(source, output):
            assert source.resolve().is_relative_to(root) and output.resolve().is_relative_to(root)
            result = subprocess.run([str(options.patcher), "build-dlc", str(source), str(output)], capture_output=True, text=True,
                timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            assert result.returncode == 0, result.stderr

        build(deep, middle / "inner.rpf")
        build(middle, outer / "middle.rpf")
        target = root / "layer-0.rpf"  # Must not collide with any detached staging child.
        build(outer, target)
        probe, payload = root / "probe", root / "payload"
        selected = "middle.rpf!inner.rpf!global.gxt2"
        other = "middle.rpf!inner.rpf!other.gxt2"

        def run(command, entry, *args):
            assert target.resolve().is_relative_to(root)
            return subprocess.run([str(options.patcher), command, str(options.game_context), str(target), entry, *map(str, args)],
                capture_output=True, text=True, timeout=90, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

        def read(entry):
            probe.unlink(missing_ok=True)
            result = run("extract-exact-nested-entry" if "!" in entry else "extract-exact-entry", entry, probe)
            assert result.returncode == 0, result.stderr
            return probe.read_bytes()

        def write(entry, before, after, ok=True, declared=None):
            payload.write_bytes(after)
            original_archive = target.read_bytes()
            result = run("replace-exact-nested-entry", entry, payload, sha(before), declared or sha(after))
            assert (result.returncode == 0) is ok, result.stderr
            if not ok: assert target.read_bytes() == original_archive
            assert not list(root.glob(".allin1-member-*"))

        assert read(selected) == b"deep original"
        assert read("middle.rpf!global.gxt2") == b"middle decoy"
        assert read("global.gxt2") == b"root decoy"
        write(selected, b"wrong current", b"new", ok=False)
        write(selected, b"deep original", b"new", ok=False, declared="f" * 64)
        write("missing.rpf!global.gxt2", b"deep original", b"new", ok=False)
        lock = target.with_name(target.name + ".allin1-member.lock")
        lock.write_bytes(b"existing test lock")
        write(selected, b"deep original", b"new", ok=False)
        assert lock.read_bytes() == b"existing test lock"
        lock.unlink()
        write(selected, b"deep original", b"edited deep")
        assert read(selected) == b"edited deep"
        applied = target.read_bytes()
        write(selected, b"deep original", b"edited deep")
        assert target.read_bytes() == applied
        write(other, b"unrelated original", b"later unrelated edit")
        write(selected, b"edited deep", b"deep original")
        assert read(selected) == b"deep original" and read(other) == b"later unrelated edit"
        assert read("middle.rpf!global.gxt2") == b"middle decoy" and read("global.gxt2") == b"root decoy"
        assert not lock.exists()
    print("Two-layer native replace/restore, decoys, unrelated later edit, checksums, missing parent, lock and idempotence passed; real GTA unchanged.", flush=True)


if __name__ == "__main__":
    main()
