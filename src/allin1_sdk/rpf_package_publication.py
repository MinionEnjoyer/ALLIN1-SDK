"""Publish a verified text RPF build using the existing ALLIN1 mod contract."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from allin1_sdk.gxt2_workspace import Gxt2Workspace
from allin1_sdk.mods import ModManifest, open_mod_package, MAX_PACKAGE_ARCHIVE_MEMBER_BYTES
from allin1_sdk.mod_package_contract import _safe_path, split_nested_rpf_entry

REPORT_LIMIT = 8 * 1024**2
SHA = re.compile(r"[a-f0-9]{64}")


def _hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _text(value, label, limit):
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or value != value.strip() or any(ord(c) < 32 for c in value)):
        raise ValueError(f"{label} must be nonempty, bounded text without surrounding whitespace or control characters")
    return value


def _bounded_file(path, limit):
    from allin1_sdk.gxt2_desktop import _path
    checked = _path(str(path))
    if not checked.is_file() or not 0 < checked.stat().st_size <= limit:
        raise ValueError(f"Missing or oversized RPF build file: {path.name}")
    return checked


def _load_json(path):
    value = json.loads(_bounded_file(path, REPORT_LIMIT).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("RPF build evidence must be a JSON object")
    return value


def _prepare(root, entries, state, source_package, metadata, destination, *, mode="whole_archive", check_space=True):
    from allin1_sdk.gxt2_desktop import _digest, _path
    if os.name != "nt":
        raise ValueError("RPF ZIP publication currently requires Windows exclusive rename")
    source = _path(source_package)
    if not source.is_dir():
        raise ValueError("Choose the verified RPF build folder, not a loose archive")
    if destination.suffix.casefold() != ".zip" or destination.is_relative_to(source):
        raise ValueError("Choose a new .zip outside the RPF build folder")
    binding = state["source_binding"]
    if not binding:
        raise ValueError("ALLIN1 RPF publication requires an archive-bound text workspace")
    if mode not in ("whole_archive", "member"):
        raise ValueError("Publication mode must be whole_archive or member")
    member_only = mode == "member"
    entry = None
    schema = 1
    if member_only:
        layer, separator, entry = binding["entry_id"].partition("::")
        if not separator or "::" in entry:
            raise ValueError("Malformed exact RPF member binding")
        entry = _safe_path(entry, "Exact RPF member")
        schema = 4 if layer else 3
        if layer:
            entry = "!".join(split_nested_rpf_entry(layer + "!" + entry))
    if binding.get("gta_path") and destination.is_relative_to(Path(binding["gta_path"]).resolve()):
        raise ValueError("Export outside the selected GTA folder")
    report_path = source / "rpf-package.json"
    report = _load_json(report_path)
    archive_name = Path(binding["outer_archive"]).name
    archive_relative = f"archive/{archive_name}"
    payload_relative = "payload/replacement.gxt2"
    payload_hash = hashlib.sha256(Gxt2Workspace.encode(entries)).hexdigest()
    archive = _bounded_file(source / archive_relative, MAX_PACKAGE_ARCHIVE_MEMBER_BYTES)
    dictionary = _bounded_file(source / payload_relative, 128 * 1024**2)
    archive_hash = _hash(archive)
    if (report.get("schema_version") != 1 or report.get("operation") != "gxt2_rpf_package"
            or report.get("status") != "verified" or report.get("source_unchanged") is not True
            or report.get("game_write_performed") is not False or report.get("installable_allin1_package") is not False
            or report.get("source_binding") != binding or report.get("workspace") != str(root)
            or report.get("workspace_state_sha256") != _digest(state)
            or report.get("archive") != {"path": archive_relative, "size": archive.stat().st_size, "sha256": archive_hash}
            or report.get("replacement") != {"entry_id": binding["entry_id"], "path": payload_relative, "sha256": payload_hash}
            or _hash(dictionary) != payload_hash):
        raise ValueError("RPF build evidence does not match this saved workspace and archive; rebuild before exporting")
    reviewed = report.get("review", {})
    comparisons = report.get("verification")
    if (not isinstance(reviewed, dict) or reviewed.get("archive_name") != archive_name
            or reviewed.get("archive_sha256") != binding["outer_archive_sha256"]
            or reviewed.get("entry_id") != binding["entry_id"] or reviewed.get("payload_sha256") != payload_hash
            or reviewed.get("original_sha256") != state["source_sha256"] or reviewed.get("edition") != binding["edition"]
            or not isinstance(comparisons, list) or not 0 < len(comparisons) <= 25000
            or reviewed.get("verified_payloads") != len(comparisons)):
        raise ValueError("Incomplete RPF verification evidence")
    identities = set()
    for row in comparisons:
        if (not isinstance(row, dict) or not isinstance(row.get("entry_id"), str)
                or row["entry_id"] in identities or not SHA.fullmatch(str(row.get("before_sha256", "")))
                or not SHA.fullmatch(str(row.get("after_sha256", "")))
                or row.get("changed") is not (row["entry_id"] == binding["entry_id"])
                or (not row["changed"] and row["before_sha256"] != row["after_sha256"])
                or (row["changed"] and row["after_sha256"] != payload_hash)):
            raise ValueError("Inconsistent RPF payload verification evidence")
        identities.add(row["entry_id"])
    if binding["entry_id"] not in identities:
        raise ValueError("Changed dictionary is missing from RPF verification evidence")
    validation_path = source / (payload_relative + ".gxt2-validation.json")
    validation = _load_json(validation_path)
    if (validation.get("sha256") != payload_hash or validation.get("source_binding") != binding
            or validation.get("original_sha256") != state["source_sha256"]):
        raise ValueError("Dictionary validation report does not match the RPF build")
    if not isinstance(metadata, dict) or set(metadata) != {"id", "name", "version", "author", "target"}:
        raise ValueError("Package metadata requires id, name, version, author and exact target")
    settings = {key: _text(metadata.get(key), key, limit) for key, limit in
                (("id", 64), ("name", 120), ("version", 64), ("author", 120), ("target", 512))}
    target = _safe_path(settings["target"], "Archive destination")
    if (not target.startswith("mods/") or "!" in target or Path(target).suffix.casefold() != ".rpf"
            or Path(target).name != archive_name):
        raise ValueError("Archive destination must be an exact mods/ .rpf path preserving the original filename")
    settings["target"] = target
    edition = binding["edition"].casefold()
    if edition not in {"legacy", "enhanced"}:
        raise ValueError("RPF packages require one known source edition")
    payload_member = payload_relative if member_only else f"payload/{archive_name}"
    shipped_file = dictionary if member_only else archive
    shipped_hash = payload_hash if member_only else archive_hash
    description = (f"Exact dictionary replacement; requires schema-{schema} support and matching original member checksum."
                   if member_only else "Whole-archive text replacement exported by ALLIN1 SDK. Review the target before installation.")
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    manifest_text = "\n".join([
        f"schema_version = {schema}", f"id = {quote(settings['id'])}", f"name = {quote(settings['name'])}",
        f"version = {quote(settings['version'])}", f"author = {quote(settings['author'])}", 'type = "rpf"',
        f"description = {quote(description)}",
        f"editions = {quote([edition])}", 'dependencies = ["openrpf"]', "conflicts = []", "dlc_packs = []", "",
        *( ["[[rpf_entries]]", f"source = {quote(payload_member)}", f"archive = {quote(target)}", f"entry = {quote(entry)}",
            f"sha256 = {quote(payload_hash)}", f"original_sha256 = {quote(state['source_sha256'])}"] if member_only else
           ["[[files]]", f"source = {quote(payload_member)}", f"destination = {quote(target)}", f"sha256 = {quote(archive_hash)}"] ), "",
    ])
    with tempfile.TemporaryDirectory(prefix="allin1-rpf-manifest-") as temporary:
        manifest_path = Path(temporary) / "mod.toml"
        manifest_path.write_text(manifest_text, encoding="utf-8")
        manifest = ModManifest.load(manifest_path, validate_payload=False)
        if manifest.mod_id != settings["id"]:
            raise ValueError("Use a lowercase package ID")
    source_hashes = {archive_relative: archive_hash, payload_relative: payload_hash,
                     "rpf-package.json": _hash(report_path), payload_relative + ".gxt2-validation.json": _hash(validation_path)}
    # Portable export evidence deliberately excludes local workspace and GTA paths.
    evidence = {"schema_version": 1, "operation": "allin1_rpf_publication", "package": settings,
                "edition": edition, "source_archive": archive_name, "source_archive_sha256": binding["outer_archive_sha256"],
                "archive_sha256": archive_hash, "edited_entry": binding["entry_id"], "dictionary_sha256": payload_hash,
                "verified_payloads": len(comparisons), "build_report_sha256": source_hashes["rpf-package.json"],
                "publication_mode": mode, "manifest_schema_version": schema, "original_sha256": state["source_sha256"] if member_only else None,
                "whole_archive_replacement": not member_only, "dlc_registration_performed": False, "install_performed": False}
    warning = (f"Only this exact archive member is replaced: {entry}\n"
               f"Required original member SHA-256: {state['source_sha256']}\n"
               f"Requires a Launcher with schema-{schema} exact-member support and its matching native helper. Older Launchers reject this package.\n"
               "Do not downgrade the schema. Installation refuses a missing or changed original member.\n"
               "Unrelated archive members are not shipped. The Launcher backs up/restores this member; uninstall an existing version before updating.\n"
               if member_only else "This package replaces the ENTIRE RPF at that target, not just one dictionary.\n"
               "Installation can replace unrelated edits already in that archive. Review conflicts and backups in ALLIN1 before installing.\n")
    readme = (f"{settings['name']} {settings['version']}\nAuthor: {settings['author']}\nEdition: {edition}\n"
              f"Install target: {target}\n\n{warning}"
              "Requires OpenRPF. No DLC pack registration is included; this is a replacement for an existing archive.\n"
              "The SDK export did not install files, upload content, or change GTA. Only distribute content you are permitted to share.\n")
    generated = {"mod.toml": manifest_text.encode("utf-8"), "allin1.rpf-build.json": _json_bytes(evidence), "README.txt": readme.encode("utf-8")}
    members = [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()} for name, data in generated.items()]
    members.append({"path": payload_member, "size": shipped_file.stat().st_size, "sha256": shipped_hash})
    members.sort(key=lambda row: row["path"])
    total = sum(row["size"] for row in members)
    required = total * 2 + 64 * 1024**2
    if check_space and (shutil.disk_usage(destination.parent).free < required or shutil.disk_usage(tempfile.gettempdir()).free < total + 64 * 1024**2):
        raise ValueError("Not enough disk space to publish and re-open the ALLIN1 ZIP")
    value = {"source_package": str(source), "metadata": settings, "edition": edition, "archive_sha256": archive_hash,
             "source_files": source_hashes, "members": members, "total_bytes": total, "required_free_bytes": required,
             "manifest_text": manifest_text, "publication_mode": mode, "manifest_schema_version": schema,
             "entry": entry, "original_sha256": state["source_sha256"] if member_only else None, "payload_sha256": shipped_hash,
             "whole_archive_replacement": not member_only, "install_performed": False,
             "dlc_registration_performed": False, "upload_performed": False}
    return value, generated, shipped_file, payload_member


def review(root, entries, state, source_package, metadata, destination, mode="whole_archive"):
    return _prepare(root, entries, state, source_package, metadata, destination, mode=mode)[0]


def build(root, entries, state, destination, reviewed, review_sha256):
    from allin1_sdk.gxt2_desktop import _context, _digest, _path
    current, generated, archive, payload_member = _prepare(root, entries, state, reviewed["source_package"], reviewed["metadata"], destination, mode=reviewed["publication_mode"])
    if current != reviewed:
        raise ValueError("RPF package or metadata changed after review")
    with tempfile.TemporaryDirectory(prefix=".allin1-rpf-publish-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "package.zip"
        expected = {row["path"]: row for row in reviewed["members"]}
        with zipfile.ZipFile(staging, "x", compression=zipfile.ZIP_STORED, allowZip64=True) as package:
            for member in sorted(expected):
                info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.file_size = expected[member]["size"]
                with package.open(info, "w", force_zip64=info.file_size >= 2 * 1024**3) as output:
                    if member == payload_member:
                        with archive.open("rb") as input_stream:
                            written = 0
                            for chunk in iter(lambda: input_stream.read(1024**2), b""):
                                written += len(chunk)
                                if written > expected[member]["size"]:
                                    raise ValueError("RPF payload grew after review")
                                output.write(chunk)
                    else:
                        output.write(generated[member])
        with zipfile.ZipFile(staging) as package:
            if package.namelist() != sorted(expected):
                raise ValueError("Export ZIP member inventory differs from review")
            for info in package.infolist():
                with package.open(info) as stream:
                    actual_hash = hashlib.file_digest(stream, "sha256").hexdigest()
                if info.file_size != expected[info.filename]["size"] or actual_hash != expected[info.filename]["sha256"]:
                    raise ValueError("Export ZIP payload changed while reading")
        with open_mod_package(staging) as manifest:
            member_only = reviewed["publication_mode"] == "member"
            if (manifest.mod_id != reviewed["metadata"]["id"] or manifest.schema_version != reviewed["manifest_schema_version"]
                    or manifest.dlc_packs or manifest.editions != (reviewed["edition"],) or manifest.dependencies != ("openrpf",)):
                raise ValueError("Export ZIP does not match the ALLIN1 manifest contract")
            if member_only:
                if (manifest.files or len(manifest.rpf_entries) != 1
                        or manifest.rpf_entries[0].entry.as_posix() != reviewed["entry"]
                        or manifest.rpf_entries[0].archive.as_posix() != reviewed["metadata"]["target"]
                        or manifest.rpf_entries[0].source.as_posix() != payload_member
                        or manifest.rpf_entries[0].sha256 != reviewed["payload_sha256"]
                        or manifest.rpf_entries[0].original_sha256 != reviewed["original_sha256"]):
                    raise ValueError("Export ZIP does not match the exact RPF member contract")
            elif (len(manifest.files) != 1 or manifest.rpf_entries or manifest.files[0].sha256 != reviewed["archive_sha256"]):
                raise ValueError("Export ZIP does not match the whole-archive contract")
        _, fresh_entries, fresh_state, _ = _context({"workspace": str(root)})
        if _digest(fresh_state) != _digest(state) or _prepare(root, fresh_entries, fresh_state, reviewed["source_package"], reviewed["metadata"], destination, mode=reviewed["publication_mode"], check_space=False)[0] != reviewed:
            raise ValueError("Workspace or RPF build changed during export; staged ZIP discarded")
        archive_hash, archive_size = _hash(staging), staging.stat().st_size
        _path(str(destination), new=True, writable=True)
        staging.rename(destination)  # Windows refuses existing files/directories.
    return {"kind": "gxt2_rpf_published", "archive": str(destination), "sha256": archive_hash, "archive_size": archive_size,
            "package_id": reviewed["metadata"]["id"], "edition": reviewed["edition"], "target": reviewed["metadata"]["target"],
            "payload_sha256": reviewed["payload_sha256"], "publication_mode": reviewed["publication_mode"],
            "manifest_schema_version": reviewed["manifest_schema_version"], "entry": reviewed["entry"],
            "original_sha256": reviewed["original_sha256"], "members": reviewed["members"], "review_sha256": review_sha256,
            "file_write_performed": True, "game_write_performed": False, "install_performed": False, "upload_performed": False}
