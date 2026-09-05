"""Stage a self-contained Tauri resource home, independent of the Launcher."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from scripts.package_release import _copy_authoring_resources, _validate_example_sources
from allin1_sdk.release_paths import no_links, tree_files


def stage_resources(root: Path, rpf_dir: Path, build_identity: Path | None = None,
                    *, destination: Path | None = None) -> Path:
    root = no_links(root).resolve(strict=True)
    rpf_dir = no_links(rpf_dir).resolve(strict=True)
    tree_files(rpf_dir)
    for name in ("assets", "sdk", "docs", "examples", "runtime/VehicleWorkbenchAxles"):
        tree_files(root / name)
    # Self-contained publish includes the runtime, not just a framework-dependent apphost.
    for name in ("RpfPatcher.exe", "RpfPatcher.dll", "coreclr.dll", "hostfxr.dll"):
        if not (rpf_dir / name).is_file():
            raise ValueError(f"Self-contained RpfPatcher publish is incomplete: {name}")
    _validate_example_sources(root)
    target = root / "desktop" / "src-tauri" / "standalone-resources"
    if destination is not None:
        target = no_links(destination).absolute()
        if not target.is_relative_to(root / "build") or target == root / "build":
            raise ValueError("Diagnostic resources must be inside this checkout's build directory")
        if target.exists():
            raise FileExistsError("Diagnostic staging never replaces existing resources")
    no_links(target)
    if target.exists():
        tree_files(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.resolve() != target or target.is_symlink():
        raise ValueError("Desktop resource staging directory must not redirect elsewhere")
    with tempfile.TemporaryDirectory(prefix=".stage-resources-", dir=target.parent) as directory:
        staged = Path(directory)
        for name in ("assets", "sdk"):
            shutil.copytree(root / name, staged / name)
        for name in ("README.md", "LICENSE"):
            shutil.copy2(root / name, staged / name)
        _copy_authoring_resources(root, staged)
        shutil.copytree(rpf_dir, staged / "tools" / "RpfPatcher")
        if build_identity is not None:
            shutil.copy2(no_links(build_identity), staged / "build-identity.json")
        license_path = no_links(root / "desktop/src-tauri/windows/TAURI-LICENSE-MIT")
        (staged / "licenses").mkdir()
        shutil.copy2(license_path, staged / "licenses/tauri-installer-MIT.txt")
        _validate_example_sources(staged)
        manifest = {
            path.relative_to(staged).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(staged.rglob("*")) if path.is_file()
        }
        (staged / "resource-checksums.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
        # This exact, validated, generated directory is replaced to remove stale payloads.
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(staged, target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpf-dir", type=Path, required=True)
    parser.add_argument("--build-identity", type=Path)
    args = parser.parse_args()
    print(stage_resources(Path(__file__).resolve().parents[1], args.rpf_dir, args.build_identity))


if __name__ == "__main__":
    main()
