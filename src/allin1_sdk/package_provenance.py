"""Complete source snapshots for existing managed project package producers."""
import json
from pathlib import Path

from allin1_sdk import artifact_identity
from allin1_sdk.artifact_contract import digest
from allin1_sdk.release_paths import no_links, tree_files
from allin1_sdk.runtime_provenance import file_hash


def snapshot(source):
    root = no_links(Path(source).expanduser())
    files = {"source": root} if root.is_file() else tree_files(root)
    if not files or len(files) > 25000:
        raise ValueError("Package source provenance requires 1–25,000 files")
    return {name: file_hash(file) for name, file in files.items()}


def complete(stage, source, inputs, build, request, *, edition, reports, resource_root):
    """Seal an installable folder without truncating its source inventory.

    The full bounded input inventory is shipped as evidence; its digest is the
    artifact's input, avoiding the install envelope's 2,000-file input cap.
    """
    if (stage / "sdk-artifact.json").exists() or (stage / "sdk-inputs.json").exists():
        raise ValueError("Staging already contains SDK provenance")
    evidence = {"schema_version": 1, "operation": "managed_project_package",
                "source_files": inputs, "request": request, "game_acceptance": "not_tested"}
    encoded = (json.dumps(evidence, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(encoded) > 4 * 1024**2:
        raise ValueError("Package source evidence exceeds 4 MiB; narrow the source")
    (stage / "sdk-inputs.json").write_bytes(encoded)
    outputs = {name: file_hash(file) for name, file in tree_files(stage).items()}
    artifact = artifact_identity.manifest(build, {"source-inventory.json": digest(inputs),
        "build-request.json": digest(request)}, outputs, edition=edition,
        reports=[outputs[name] for name in reports], changes=evidence)
    encoded = (json.dumps(artifact, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(encoded) > 4 * 1024**2:
        raise ValueError("SDK artifact envelope exceeds 4 MiB")
    (stage / "sdk-artifact.json").write_bytes(encoded)
    if snapshot(source) != inputs or artifact_identity.current(resource_root=resource_root) != build:
        raise ValueError("Source or SDK/helper changed during package construction")
    return artifact
