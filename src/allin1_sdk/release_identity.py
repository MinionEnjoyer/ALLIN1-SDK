"""Read-only release identity checks; dirty candidates cannot become releases."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from allin1_sdk.release_paths import contained, filesystem_path, no_links, strict_json, tree_files, unique_paths


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with filesystem_path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_inventory(root: Path, manifest_name: str = "resource-checksums.json") -> dict:
    files = tree_files(root)
    manifest = strict_json(filesystem_path(contained(root, manifest_name)).read_bytes())
    if not isinstance(manifest, dict):
        raise ValueError("Invalid resource checksum manifest")
    unique_paths(list(manifest))
    if set(manifest) != files.keys() - {manifest_name}:
        raise ValueError("Resource manifest does not exactly match staged payloads")
    for name, expected in manifest.items():
        if not isinstance(expected, str) or not re.fullmatch("[a-f0-9]{64}", expected) or sha256(files[name]) != expected:
            raise ValueError(f"Resource checksum mismatch: {name}")
    return manifest


def source_identity(root: Path) -> dict:
    root = no_links(root).resolve(strict=True)
    def git(*args):
        return subprocess.check_output(["git", "-C", str(root), *args], text=True, encoding="utf-8").strip()
    versions = {}
    for name in ("pyproject.toml", "desktop/src-tauri/Cargo.toml"):
        match = re.search(r'^version\s*=\s*"([^"]+)"', (root / name).read_text(encoding="utf-8"), re.M)
        if not match:
            raise ValueError(f"Missing source version: {name}")
        versions[name] = match[1]
    match = re.search(r'__version__\s*=\s*"([^"]+)"', (root / "src/allin1_sdk/__init__.py").read_text(encoding="utf-8"))
    if not match:
        raise ValueError("Missing Python SDK version")
    versions["python"] = match[1]
    for name in ("desktop/package.json", "desktop/src-tauri/tauri.conf.json"):
        versions[name] = strict_json((root / name).read_bytes())["version"]
    # Git's input inventory includes uncommitted migration sources, not ignored
    # generated executables. A dirty source digest never masquerades as HEAD.
    names = git("ls-files", "--cached", "--others", "--exclude-standard", "-z").split("\0")
    inputs = {}
    submodules = {}
    for name in sorted(set(names)):
        if not name or name.startswith((".work/", "build/")):
            continue
        path = contained(root, name)
        if path.is_file():
            inputs[name] = sha256(path)
        elif path.is_dir():
            # Gitlinks are directories, not files; bind their checked-out commit
            # and refuse dirty native dependencies instead of silently omitting them.
            sub_head = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
            sub_dirty = subprocess.check_output(["git", "-C", str(path), "status", "--porcelain"], text=True).strip()
            if sub_dirty:
                raise ValueError(f"Build dependency has uncommitted changes: {name}")
            submodules[name] = sub_head
            inputs[name] = sub_head
    digest = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"schema_version": 1, "sdk_commit": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain", "--untracked-files=normal")),
            "versions": versions, "versions_agree": len(set(versions.values())) == 1,
            "source_tree_sha256": digest, "input_count": len(inputs), "submodules": submodules}


def embedded_build_identity() -> dict | None:
    """Frozen build metadata, never guessed from the developer checkout."""
    path = Path(__file__).with_name("_build_identity.json")
    if not path.is_file():
        return None
    value = strict_json(path.read_bytes())
    from allin1_sdk import __version__
    if (not isinstance(value, dict) or value.get("schema_version") != 1
            or value.get("kind") != "sdk_build_identity" or value.get("sdk_version") != __version__):
        raise ValueError("Embedded SDK build identity is invalid or version-mismatched")
    return value


def verify_runtime_resources(root: Path, trusted_manifest: Path) -> None:
    """Compare installed resources to the manifest embedded in this sidecar.

    Unrelated root-level user data is preserved/allowed; resource directories
    must match exactly so a stale companion DLL cannot silently remain active.
    """
    expected = strict_json(trusted_manifest.read_bytes())
    if not isinstance(expected, dict) or strict_json(filesystem_path(contained(root, "resource-checksums.json")).read_bytes()) != expected:
        raise ValueError("Installed resource manifest belongs to another SDK build")
    unique_paths(list(expected))
    for name, digest in expected.items():
        if sha256(contained(root, name)) != digest:
            raise ValueError(f"Installed SDK resource differs from frozen build: {name}")
    for directory in {name.split("/", 1)[0] for name in expected if "/" in name}:
        actual = {f"{directory}/{name}" for name in tree_files(contained(root, directory))}
        if actual != {name for name in expected if name.startswith(directory + "/")}:
            raise ValueError(f"Stale or unlisted SDK resources: {directory}")


def require_reviewed_source(root: Path, reviewed_commit: str, version: str) -> dict:
    identity = source_identity(root)
    if not re.fullmatch("[0-9a-f]{40}", reviewed_commit) or identity["sdk_commit"] != reviewed_commit:
        raise ValueError("Release requires the exact independently reviewed commit")
    if identity["dirty"]:
        raise ValueError("Release refused: source tree contains unreviewed/uncommitted changes")
    if not identity["versions_agree"] or set(identity["versions"].values()) != {version}:
        raise ValueError("Release refused: Python, package, Cargo and Tauri versions disagree")
    return identity
