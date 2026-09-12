"""Build portable edition bundles from managed packages or supported OIVs.

Authoring only: no OpenIV execution, game writes, automatic edition conversion,
or copying of undeclared source trees into the distributable.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import replace
from pathlib import Path

from allin1_sdk.mods import (
    ModManifest, open_mod_package, _archive_member_path, _contained_path,
    _sha256, tomllib, MAX_PACKAGE_ARCHIVE_MEMBERS, MAX_PACKAGE_ARCHIVE_BYTES,
    MAX_PACKAGE_ARCHIVE_MEMBER_BYTES, MAX_PACKAGE_COMPRESSION_RATIO,
)
from allin1_sdk.oiv_workbench import OivWorkbench
from allin1_sdk.paths import gta_root_containing
from allin1_sdk.release_paths import no_links, strict_json
from allin1_sdk.artifact_contract import validate_manifest as validate_artifact
from allin1_sdk.mod_package_contract import validate_edition_bundle


def _extract_oivs(source: Path, names: dict[str, str], root: Path) -> dict[str, Path]:
    """Extract only explicitly selected OIVs from a bounded, inspected outer ZIP."""
    if len({name.casefold() for name in names.values()}) != len(names):
        raise ValueError("Select separate Legacy and Enhanced OIV members")
    with zipfile.ZipFile(no_links(source)) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_PACKAGE_ARCHIVE_MEMBERS:
            raise ValueError("Source ZIP contains too many members")
        members = {}
        total = 0
        for info in infos:
            relative = _archive_member_path(info)
            if relative is None or info.is_dir():
                continue
            key = relative.as_posix().casefold()
            if key in members:
                raise ValueError("Source ZIP contains duplicate member paths")
            members[key] = info
            total += info.file_size
            if (info.file_size > MAX_PACKAGE_ARCHIVE_MEMBER_BYTES
                    or total > MAX_PACKAGE_ARCHIVE_BYTES
                    or (info.file_size and (not info.compress_size
                        or info.file_size / info.compress_size > MAX_PACKAGE_COMPRESSION_RATIO))):
                raise ValueError("Source ZIP exceeds safe expansion limits")
        result = {}
        for edition, name in names.items():
            info = members.get(name.casefold())
            if info is None or Path(name).suffix.casefold() != ".oiv":
                raise ValueError(f"Missing selected OIV member for {edition}: {name}")
            target = root / (edition + ".oiv")
            count = 0
            with archive.open(info) as incoming, target.open("xb") as output:
                for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                    count += len(chunk)
                    if count > min(info.file_size, MAX_PACKAGE_ARCHIVE_MEMBER_BYTES):
                        raise ValueError("OIV exceeded its declared size")
                    output.write(chunk)
            if count != info.file_size:
                raise ValueError("OIV size changed during extraction")
            result[edition] = target
        return result


def _copy_package(manifest: ModManifest, output: Path) -> None:
    """Include install payload, descriptors and provenance; never unrelated source."""
    raw = tomllib.loads(manifest.manifest_path.read_text(encoding="utf-8"))
    paths = {"mod.toml"} | {str(item.source) for item in (*manifest.files, *manifest.rpf_entries)}
    if "allin1" in raw:
        paths.add(raw["allin1"]["content"])
    artifact_file = manifest.package_root / "sdk-artifact.json"
    artifact = None
    if artifact_file.exists():
        no_links(artifact_file)
        if artifact_file.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("SDK artifact envelope exceeds 4 MiB")
        artifact = validate_artifact(strict_json(artifact_file.read_bytes()))
        if artifact["edition"] is not None and artifact["edition"] != manifest.editions[0]:
            raise ValueError("SDK artifact declares the wrong edition")
        if not paths.issubset(artifact["outputs"]):
            raise ValueError("SDK artifact does not cover the managed package")
        paths.update(artifact["outputs"])
        paths.add("sdk-artifact.json")
    for relative in sorted(paths):
        source = _contained_path(manifest.package_root, relative)
        if artifact and relative != "sdk-artifact.json" and _sha256(source) != artifact["outputs"][relative]:
            raise ValueError(f"SDK artifact payload changed: {relative}")
        target = _contained_path(output, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def build_edition_bundle(
    output: str | Path, *, legacy: str | Path, enhanced: str | Path,
    mod_id: str, name: str, version: str, source_zip: str | Path | None = None,
) -> dict:
    """Author a new ZIP atomically; two inputs may be OIVs or managed packages.

    With source_zip, legacy/enhanced are explicit OIV member paths in that archive.
    Without it, each is a local OIV, package folder, mod.toml, or managed ZIP.
    Managed inputs retain identity and must already match the bundle.
    """
    destination = no_links(Path(output).expanduser()).resolve()
    if destination.suffix.casefold() != ".zip" or not destination.parent.is_dir():
        raise ValueError("Choose a .zip destination in an existing directory")
    if destination.exists():
        raise ValueError("Bundle destination already exists; choose a new filename")
    if gta_root_containing(destination):
        raise ValueError("Bundle exports must be outside the game installation")
    raw = dict(schema_version=5, id=mod_id, name=name, version=version,
               type="bundle", editions=["legacy", "enhanced"],
               variants={edition: {"manifest": f"{edition}/mod.toml", "sha256": "0" * 64}
                         for edition in ("legacy", "enhanced")})
    validate_edition_bundle(raw)
    inputs = {"legacy": legacy, "enhanced": enhanced}
    with tempfile.TemporaryDirectory(prefix="allin1-editions-", dir=destination.parent) as temporary:
        stage = Path(temporary)
        if source_zip:
            inputs = _extract_oivs(Path(source_zip), {k: str(v) for k, v in inputs.items()}, stage)
        bundle = stage / "bundle"
        bundle.mkdir()
        for edition, source in inputs.items():
            selected = no_links(Path(source).expanduser()).resolve()
            child_root = bundle / edition
            if selected.suffix.casefold() == ".oiv":
                workbench = OivWorkbench()
                plan = workbench.inspect(selected)
                if edition not in plan.editions:
                    raise ValueError(f"{edition} input declares a different game edition")
                if not plan.managed_exportable:
                    raise ValueError(
                        f"{edition} OIV requires review/compilation before bundling; "
                        "export it as a managed package in the OIV workbench first"
                    )
                plan = replace(plan, name=name, version=version, editions=(edition,))
                path = workbench.export_managed_package(plan, child_root)
                text = path.read_text(encoding="utf-8")
                # The exporter emits exactly one top-level id line.
                lines = text.splitlines()
                index = next(i for i, line in enumerate(lines) if line.startswith("id = "))
                lines[index] = "id = " + json.dumps(mod_id)
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            else:
                with open_mod_package(selected) as manifest:
                    if manifest.schema_version in (5, 6):
                        raise ValueError("Nested edition bundles are not supported")
                    if manifest.editions != (edition,):
                        raise ValueError(f"Managed {edition} input must declare only that edition")
                    if (manifest.mod_id, manifest.name, manifest.version) != (mod_id, name, version):
                        raise ValueError("Managed inputs must match the bundle id, name and version")
                    child_root.mkdir()
                    _copy_package(manifest, child_root)
            raw["variants"][edition]["sha256"] = _sha256(child_root / "mod.toml")
        lines = ["schema_version = 5"] + [
            f"{key} = {json.dumps(raw[key])}" for key in ("id", "name", "version", "type", "editions")
        ]
        for edition, row in raw["variants"].items():
            lines += [f"\n[variants.{edition}]", f'manifest = {json.dumps(row["manifest"])}',
                      f'sha256 = {json.dumps(row["sha256"])}']
        (bundle / "mod.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        manifest = ModManifest.load(bundle)
        archive_path = stage / "result.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(bundle.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(bundle).as_posix())
        with open_mod_package(archive_path) as packaged:
            assert packaged.schema_version == 5
        # Same-filesystem hard-link publication is atomic and cannot replace
        # an output that appeared while building. TemporaryDirectory removes
        # only its own staging link afterward.
        os.link(archive_path, no_links(destination))
        return {"path": str(destination), "sha256": _sha256(destination),
                "schema_version": 5, "id": manifest.mod_id,
                "editions": list(manifest.editions),
                "variants": raw["variants"]}
