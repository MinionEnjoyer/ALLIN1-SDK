"""Build the checksum-verifiable Windows archive consumed by the launcher."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from allin1_sdk.release_paths import no_links, tree_files
from allin1_sdk.release_identity import require_reviewed_source
from allin1_sdk.self_update import inspect_release_archive


_AUTHORING_RESOURCE_TREES = (
    Path("docs"),
    Path("examples"),
    Path("runtime") / "VehicleWorkbenchAxles",
)
_AUTHORING_RESOURCE_IGNORED_PARTS = {
    ".git",
    ".pytest_cache",
    "__pycache__",
    "bin",
    "build",
    "dist",
    "obj",
    "out",
}
_AUTHORING_RESOURCE_IGNORED_SUFFIXES = {
    ".asi",
    ".dll",
    ".exe",
    ".pdb",
    ".pyc",
}
_ROOT_DOCUMENTATION = (
    Path("RELEASE_NOTES.md"), Path("CODE_SIGNING_POLICY.md"),
    Path("RELEASE_SIGNING.md"), Path("desktop/README.md"),
)
_REQUIRED_AUTHORING_RESOURCES = (
    *_ROOT_DOCUMENTATION,
    Path("docs/README.md"), Path("docs/catalog.json"),
    Path("docs/sdk-guide.md"), Path("docs/release-0.6.4.md"),
    Path("docs/cli-reference.md"), Path("docs/validation.md"),
    Path("assets") / "axle-prefabs.json",
    Path("assets") / "visual-tyre-packages.json",
    Path("docs") / "axle-prefabs.md",
    Path("docs") / "oiv-story-packages.md",
    Path("examples") / "axle-prefabs" / "three-axle-bus.json",
    Path("examples") / "oiv-axle-bundles" / "vehicle-only-request.template.json",
    Path("runtime") / "VehicleWorkbenchAxles" / "CMakeLists.txt",
    Path("runtime") / "VehicleWorkbenchAxles" / "README.md",
    Path("runtime") / "VehicleWorkbenchAxles" / "include"
    / "vehicle_workbench_axles" / "types.hpp",
    Path("runtime") / "VehicleWorkbenchAxles" / "include"
    / "vehicle_workbench_axles" / "runtime_settings_document.hpp",
    Path("runtime") / "VehicleWorkbenchAxles" / "profiles" / "compatibility.json",
    Path("runtime") / "VehicleWorkbenchAxles" / "schemas" / "axle-config.schema.json",
    Path("runtime") / "VehicleWorkbenchAxles" / "src" / "runtime.cpp",
    Path("runtime") / "VehicleWorkbenchAxles" / "src"
    / "runtime_settings_document.cpp",
    Path("runtime") / "VehicleWorkbenchAxles" / "tests" / "core_tests.cpp",
    Path("runtime") / "VehicleWorkbenchAxles" / "tools" / "config_validator.cpp",
    Path("runtime") / "VehicleWorkbenchAxles" / "tools" / "settings_editor.cpp",
)


def _version(value: str) -> str:
    normalized = value.lstrip("vV")
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", normalized):
        raise argparse.ArgumentTypeError(f"invalid release version: {value}")
    return normalized


def _build_id(value: str) -> str:
    normalized = value.strip()
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._:+/-]{0,127}", normalized):
        raise argparse.ArgumentTypeError(f"invalid release build ID: {value}")
    return normalized


def _validate_example_sources(root: Path) -> None:
    """Refuse a release whose bundled examples point outside its payload."""
    source_root = root.resolve()
    examples_root = source_root / "sdk" / "examples"
    if not examples_root.is_dir():
        return
    missing: list[str] = []
    for manifest_path in sorted(examples_root.glob("*/addon.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"Bundled SDK example is not valid JSON: {manifest_path}: {exc}"
            ) from exc
        records = [
            *data.get("nodes", []), *data.get("install_steps", []),
        ]
        for record in records:
            if not isinstance(record, dict) or not record.get("source"):
                continue
            relative = Path(str(record["source"]))
            candidate = (source_root / relative).resolve()
            try:
                candidate.relative_to(source_root)
            except ValueError:
                missing.append(f"{manifest_path.name}: unsafe source {relative}")
                continue
            if not candidate.is_file():
                missing.append(f"{manifest_path.name}: missing source {relative}")
    if missing:
        raise ValueError(
            "Bundled SDK examples have unresolved source files:\n- "
            + "\n- ".join(missing)
        )


def _copy_authoring_resources(root: Path, app_dir: Path) -> None:
    """Copy auditable authoring resources without generated runtime binaries."""
    missing = [
        relative.as_posix()
        for relative in _REQUIRED_AUTHORING_RESOURCES
        if not (root / relative).is_file()
    ]
    if missing:
        raise ValueError(
            "Required SDK authoring resources are missing:\n- "
            + "\n- ".join(missing)
        )

    for relative in _ROOT_DOCUMENTATION:
        source = no_links(root / relative)
        destination = no_links(app_dir / relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    for relative_root in _AUTHORING_RESOURCE_TREES:
        source_root = root / relative_root
        if not source_root.is_dir():
            raise ValueError(f"SDK authoring resource tree is missing: {source_root}")
        for source in sorted(source_root.rglob("*")):
            relative = source.relative_to(source_root)
            if any(
                part.casefold() in _AUTHORING_RESOURCE_IGNORED_PARTS
                for part in relative.parts
            ):
                continue
            if source.is_symlink():
                raise ValueError(f"SDK authoring resource may not be a symlink: {source}")
            if not source.is_file():
                continue
            if source.suffix.casefold() in _AUTHORING_RESOURCE_IGNORED_SUFFIXES:
                continue
            target = app_dir / relative_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _copy_runtime(root: Path, app_dir: Path, rpf_dir: Path) -> None:
    for name in (
        "ALLIN1-SDK-Desktop.exe", "allin1-sdk.exe", "ALLIN1-SDK-Agent.exe",
        "ALLIN1-SDK-Updater.exe",
    ):
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
    _copy_authoring_resources(root, app_dir)
    shutil.copytree(rpf_dir, app_dir / "tools" / "RpfPatcher", dirs_exist_ok=True)
    for name in ("README.md", "LICENSE"):
        shutil.copy2(root / name, app_dir / name)


def _iter_payload(app_dir: Path):
    for path in sorted(app_dir.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_file() and path != app_dir / "checksums.json":
            yield path.relative_to(app_dir).as_posix(), path


def package_release(
    root: Path, app_dir: Path, rpf_dir: Path, output: Path, version: str,
    build_id: str,
) -> tuple[Path, Path]:
    version = _version(version)
    _build_id(build_id)
    for directory in (root, app_dir, rpf_dir, output):
        no_links(directory)
    inputs = tree_files(app_dir)
    tree_files(rpf_dir)
    for name in ("sdk", "assets", "docs", "examples", "runtime/VehicleWorkbenchAxles"):
        tree_files(root / name)
    if output.resolve().is_relative_to(app_dir.resolve()) or output.resolve().is_relative_to(rpf_dir.resolve()):
        raise ValueError("Release output must not be inside an input payload")
    archive_path = output / f"ALLIN1-SDK-{version}-win-x64.zip"
    if archive_path.exists() or archive_path.with_name(archive_path.name + ".sha256").exists():
        raise FileExistsError("Release artifact already exists; never overwrite/reuse a release identity")
    _validate_example_sources(root)
    # Build in a new owned directory; merging resource trees leaves removed
    # DLLs/docs in the package and makes the staged and packaged bytes disagree.
    with tempfile.TemporaryDirectory(prefix="allin1-sdk-package-") as directory:
        fresh = Path(directory)
        for name, source in inputs.items():
            if (name.split("/")[0] in {"sdk", "assets", "docs", "examples", "runtime"}
                    or name.startswith("tools/RpfPatcher/") or name in {"release.json", "checksums.json", "README.md", "LICENSE"}):
                continue
            target = fresh / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return _package_fresh(root, fresh, rpf_dir, output, version, build_id)


def _package_fresh(root, app_dir, rpf_dir, output, version, build_id):
    _validate_example_sources(root)
    _copy_runtime(root, app_dir, rpf_dir)
    metadata = {
        "product": "ALLIN1-SDK",
        "version": version,
        "build_id": _build_id(build_id),
        "platform": "win-x64",
        "entrypoint": "ALLIN1-SDK-Desktop.exe",
        "cli_entrypoint": "allin1-sdk.exe",
        "agent_entrypoint": "ALLIN1-SDK-Agent.exe",
        "updater_entrypoint": "ALLIN1-SDK-Updater.exe",
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
    with zipfile.ZipFile(archive_path, "x", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        for path in sorted(app_dir.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if not path.is_file():
                continue
            relative = path.relative_to(app_dir).as_posix()
            info = zipfile.ZipInfo(relative, (2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    inspect_release_archive(archive_path, version)
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
    parser.add_argument("--build-id", type=_build_id)
    parser.add_argument("--reviewed-commit", required=True, help="Exact independently reviewed clean source commit")
    args = parser.parse_args()
    build_id = args.build_id
    if build_id is None:
        try:
            build_id = _build_id(os.environ.get("GITHUB_SHA", ""))
        except argparse.ArgumentTypeError as exc:
            parser.error("--build-id is required when GITHUB_SHA is unavailable or invalid")
    root = Path(__file__).resolve().parents[1]
    require_reviewed_source(root, args.reviewed_commit, args.version)
    if build_id != args.reviewed_commit:
        parser.error("Release build ID must equal the reviewed source commit")
    archive, checksum = package_release(
        root, args.app_dir.resolve(), args.rpf_dir.resolve(), args.output.resolve(),
        args.version, build_id,
    )
    print(archive)
    print(checksum)


if __name__ == "__main__":
    main()
