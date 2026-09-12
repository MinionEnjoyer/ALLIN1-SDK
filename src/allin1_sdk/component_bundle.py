"""Author schema-6 collections without changing component identities or payloads."""
import json
import os
import tempfile
import zipfile
from pathlib import Path

from allin1_sdk.edition_bundle import _copy_package
from allin1_sdk.mods import ModManifest, open_mod_package, _sha256
from allin1_sdk.mod_package_contract import validate_component_bundle
from allin1_sdk.paths import gta_root_containing
from allin1_sdk.release_paths import no_links


def build_component_bundle(output, *, legacy, enhanced, mod_id, name, version):
    """Ordered managed inputs, not nested bundles or executable OIV recipes."""
    destination = no_links(Path(output).expanduser()).resolve()
    if destination.suffix.casefold() != ".zip" or not destination.parent.is_dir():
        raise ValueError("Choose a .zip destination in an existing directory")
    if destination.exists():
        raise ValueError("Bundle destination already exists; choose a new filename")
    if gta_root_containing(destination):
        raise ValueError("Bundle exports must be outside the game installation")
    inputs = {"legacy": legacy, "enhanced": enhanced}
    if any(not isinstance(items, (list, tuple)) or not 1 <= len(items) <= 32 for items in inputs.values()):
        raise ValueError("Provide 1–32 ordered managed packages for each edition")
    data = dict(schema_version=6, id=mod_id, name=name, version=version, type="collection",
                editions=list(inputs), variants={
                    edition: {"components": [
                        {"manifest": f"{edition}/{index:02d}/mod.toml", "sha256": "0" * 64}
                        for index in range(1, len(items) + 1)]}
                    for edition, items in inputs.items()})
    validate_component_bundle(data)
    with tempfile.TemporaryDirectory(prefix="allin1-components-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        root = stage / "bundle"
        root.mkdir()
        inventory = []
        for edition, sources in inputs.items():
            for source, row in zip(sources, data["variants"][edition]["components"]):
                with open_mod_package(no_links(Path(source))) as child:
                    if child.schema_version in (5, 6):
                        raise ValueError("Nested edition bundles are not supported")
                    if child.editions != (edition,):
                        raise ValueError("Components must declare only their matching edition")
                    target = root / row["manifest"]
                    target.parent.mkdir(parents=True)
                    _copy_package(child, target.parent)
                    row["sha256"] = _sha256(target)
                    inventory.append(dict(edition=edition, id=child.mod_id, name=child.name,
                                          version=child.version, schema_version=child.schema_version,
                                          files=len(child.files), rpf_entries=len(child.rpf_entries)))
        lines = [f"{key} = {json.dumps(data[key])}" for key in (
            "schema_version", "id", "name", "version", "type", "editions")]
        for edition, variant in data["variants"].items():
            for row in variant["components"]:
                lines += [f"\n[[variants.{edition}.components]]",
                          f"manifest = {json.dumps(row['manifest'])}",
                          f"sha256 = {json.dumps(row['sha256'])}"]
        (root / "mod.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        ModManifest.load(root)
        archive_path = stage / "result.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(root.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(root).as_posix())
        with open_mod_package(archive_path) as packaged:
            assert packaged.schema_version == 6
        os.link(archive_path, no_links(destination))
    return dict(path=str(destination), sha256=_sha256(destination), schema_version=6,
                id=mod_id, editions=list(inputs), components=inventory)
