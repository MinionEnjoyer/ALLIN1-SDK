"""Build the checksum-verifiable Windows archive consumed by the launcher."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path


def _version(value: str) -> str:
    normalized = value.lstrip("vV")
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", normalized):
        raise argparse.ArgumentTypeError(f"invalid release version: {value}")
    return normalized


def _copy_runtime(root: Path, app_dir: Path, rpf_dir: Path) -> None:
    for name in ("ALLIN1-SDK.exe", "ALLIN1-SDK-Agent.exe"):
        executable = app_dir / name
        signature = b""
        if executable.is_file():
            with executable.open("rb") as stream:
                signature = stream.read(2)
        if signature != b"MZ":
            raise ValueError(f"PyInstaller executable is missing or invalid: {executable}")
    if not (rpf_dir / "RpfPatcher.exe").is_file():
        raise ValueError(f"RpfPatcher runtime is missing: {rpf_dir}")
    shutil.copytree(root / "sdk", app_dir / "sdk", dirs_exist_ok=True)
    shutil.copytree(root / "assets", app_dir / "assets", dirs_exist_ok=True)
    shutil.copytree(rpf_dir, app_dir / "tools" / "RpfPatcher", dirs_exist_ok=True)
    for name in ("README.md", "LICENSE"):
        shutil.copy2(root / name, app_dir / name)


def _iter_payload(app_dir: Path):
    for path in sorted(app_dir.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_file() and path.name != "checksums.json":
            yield path.relative_to(app_dir).as_posix(), path


def package_release(root: Path, app_dir: Path, rpf_dir: Path, output: Path, version: str) -> tuple[Path, Path]:
    _copy_runtime(root, app_dir, rpf_dir)
    metadata = {
        "product": "ALLIN1-SDK",
        "version": version,
        "platform": "win-x64",
        "entrypoint": "ALLIN1-SDK.exe",
        "agent_entrypoint": "ALLIN1-SDK-Agent.exe",
    }
    (app_dir / "release.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    checksums = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in _iter_payload(app_dir)
    }
    (app_dir / "checksums.json").write_text(
        json.dumps(checksums, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / f"ALLIN1-SDK-{version}-win-x64.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for path in sorted(app_dir.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if not path.is_file():
                continue
            relative = path.relative_to(app_dir).as_posix()
            info = zipfile.ZipInfo(relative, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path = archive_path.with_name(archive_path.name + ".sha256")
    checksum_path.write_text(f"{digest}  {archive_path.name}\n", encoding="ascii")
    return archive_path, checksum_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--rpf-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", type=_version, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    archive, checksum = package_release(
        root, args.app_dir.resolve(), args.rpf_dir.resolve(), args.output.resolve(), args.version,
    )
    print(archive)
    print(checksum)


if __name__ == "__main__":
    main()
