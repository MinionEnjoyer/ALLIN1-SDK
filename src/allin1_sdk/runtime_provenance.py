"""Portable native-controller build inputs and edition-specific install lineage."""
import hashlib
import json
import zipfile

from allin1_sdk import artifact_identity
from allin1_sdk.artifact_contract import inventory, validate_manifest
from allin1_sdk.mods import ModManifest
from allin1_sdk.release_paths import no_links, tree_files, strict_json


def file_hash(path):
    with no_links(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def source_identity(source):
    files = {}
    for name in ("src", "include", "tests", "tools", "schemas", "profiles", "examples"):
        folder = no_links(source / name)
        if folder.is_dir():
            files.update({f"runtime/{name}/{key}": file_hash(value) for key, value in tree_files(folder).items()})
    for name in ("CMakeLists.txt", "README.md"):
        item = no_links(source / name)
        if item.is_file():
            files[f"runtime/{name}"] = file_hash(item)
    inventory(files)
    return files


def write_edition(root, build, inputs, edition, version):
    """The existing single-edition folder/ZIP is also an ALLIN1 package.

    Combined archives retain separate edition manifests; they are not a single
    installable package and never mix edition payloads into one artifact.
    """
    files = tree_files(root)
    if "mod.toml" in files or "sdk-artifact.json" in files:
        raise ValueError("Runtime staging already contains publication metadata")
    hashes = {name: file_hash(file) for name, file in files.items()}
    quote = json.dumps
    lines = ['schema_version = 1', 'id = "vehicle-workbench-axles"',
             'name = "Vehicle Workbench Axles (candidate)"', f'version = {quote(version)}',
             'author = "ALLIN1 SDK"', 'type = "mixed"',
             'description = "Locally built candidate. In-game acceptance has not been established."',
             f'editions = {quote([edition.casefold()])}', 'dependencies = ["scripthookv"]',
             'conflicts = []', 'dlc_packs = []', '']
    for name, checksum in sorted(hashes.items()):
        if name in {"build-validation-receipt.json", "VehicleWorkbenchAxles.Settings.exe"}:
            continue  # Evidence and optional standalone helper remain in the package.
        lines.extend(['[[files]]', f'source = {quote(name)}', f'destination = {quote(name)}',
                      f'sha256 = {quote(checksum)}', ''])
    (root / "mod.toml").write_text("\n".join(lines), encoding="utf-8")
    try:
        manifest = ModManifest.load(root)
        publication = {"installable_allin1_package": True,
            "install_files": [item.destination.as_posix() for item in manifest.files],
            "package_only": ["build-validation-receipt.json", "VehicleWorkbenchAxles.Settings.exe"],
            "notice": "The optional standalone settings editor remains in the export, not installed by ALLIN1."}
        hashes["mod.toml"] = file_hash(root / "mod.toml")
    except ValueError as exc:
        # Preserve existing portable custom-path builds. Never broaden the
        # Launcher's destination allowlist just to label a candidate installable.
        (root / "mod.toml").unlink()
        publication = {"installable_allin1_package": False, "install_files": [],
            "notice": f"Manual candidate layout is outside the ALLIN1 install contract: {exc}"}
    (root / "sdk-publication.json").write_text(json.dumps(publication, indent=2) + "\n", encoding="utf-8")
    hashes["sdk-publication.json"] = file_hash(root / "sdk-publication.json")
    artifact = artifact_identity.manifest(build, inputs, hashes, edition=edition,
        reports=[hashes["build-validation-receipt.json"]],
        changes={"operation": "compile_story_controller", "edition": edition,
                 "binary": "VehicleWorkbenchAxles.asi", "game_acceptance": "not_tested"})
    encoded = (json.dumps(artifact, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if len(encoded) > 4 * 1024**2:
        raise ValueError("Runtime artifact envelope exceeds 4 MiB")
    (root / "sdk-artifact.json").write_bytes(encoded)
    return artifact


def verify_edition(root, expected_id):
    files = tree_files(root)
    envelope = files.get("sdk-artifact.json")
    if envelope is None or envelope.stat().st_size > 4 * 1024**2:
        raise ValueError("Missing or oversized runtime artifact envelope")
    artifact = validate_manifest(strict_json(envelope.read_bytes()))
    if artifact["artifact_id"] != expected_id or set(files) != set(artifact["outputs"]) | {"sdk-artifact.json"}:
        raise ValueError("Runtime artifact identity or publication inventory changed")
    for name, checksum in artifact["outputs"].items():
        if file_hash(files[name]) != checksum:
            raise ValueError(f"Runtime staged payload changed: {name}")
    return {**artifact["outputs"], "sdk-artifact.json": file_hash(envelope)}


def verify_archive(path, expected):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError("Runtime archive inventory differs from its artifact")
        for name, checksum in expected.items():
            with archive.open(name) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != checksum:
                    raise ValueError(f"Runtime archive payload changed: {name}")
