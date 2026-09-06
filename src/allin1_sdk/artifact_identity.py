"""Actual executing SDK identity and a portable, content-addressed artifact trail."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys

from allin1_sdk import __version__, release_identity
from allin1_sdk.paths import project_root
from allin1_sdk.release_paths import no_links, tree_files
from allin1_sdk.artifact_contract import seal, validate_manifest


def current(*, resource_root=None):
    """Capture source AND executing/helper bytes; never equate a version to a build.

    Frozen resources are verified against the embedded checksum inventory.
    Development sources include dirty/untracked work, and are explicitly not a
    release or proof that a previously compiled helper matches those sources.
    """
    resources = no_links(Path(resource_root) if resource_root is not None else project_root())
    embedded = release_identity.embedded_build_identity()
    if getattr(sys, "frozen", False):
        if embedded is None:
            raise ValueError("Frozen SDK is missing its embedded build identity")
        trusted = Path(release_identity.__file__).with_name("resource-checksums.json")
        release_identity.verify_runtime_resources(resources, trusted)
        source = embedded
        resource_files = json.loads(trusted.read_bytes())
        mode = "frozen_verified_resources"
    else:
        # ALLIN1_SDK_HOME can select resources, but cannot change which Python
        # checkout is actually executing this code.
        source = release_identity.source_identity(Path(__file__).resolve().parents[2])
        resource_files = {}
        helper_root = no_links(resources/"tools/RpfPatcher")
        if not helper_root.is_dir():
            raise ValueError("SDK helper directory is missing")
        for file in sorted(helper_root.iterdir()):
            if file.is_file() and file.name.lower().endswith((".dll", ".exe", ".deps.json", ".runtimeconfig.json")):
                no_links(file)
                resource_files["tools/RpfPatcher/"+file.name] = release_identity.sha256(file)
        if "tools/RpfPatcher/RpfPatcher.exe" not in resource_files:
            raise ValueError("Native helper executable is missing")
        mode = "development_dirty" if source["dirty"] else "development_clean"
    executable = no_links(Path(sys.executable))
    packages = {}
    for name in ("Pillow", "lxml", "numpy", "PyInstaller"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = "not_installed"
    identity = {"schema_version":1, "kind":"sdk_execution_identity", "sdk_version":__version__,
                "mode":mode, "source":source, "resource_files":resource_files,
                "executable_sha256":release_identity.sha256(executable),
                "python_version":platform.python_version(), "python_packages":packages,
                "scope":"Content identity, not a signature or game acceptance. Development helper/source consistency and the complete external interpreter environment are not certified."}
    return seal(identity, "build_fingerprint")


def manifest(build, inputs, outputs, *, edition, reports=(), changes=None):
    result = seal({"schema_version":1, "kind":"sdk_artifact_manifest", "build":build,
                   "inputs":inputs, "outputs":outputs, "edition":edition,
                   "validation_reports":list(reports),
                   "changes_sha256":hashlib.sha256(json.dumps(changes,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()}, "artifact_id")
    validate_manifest(result)
    return result
