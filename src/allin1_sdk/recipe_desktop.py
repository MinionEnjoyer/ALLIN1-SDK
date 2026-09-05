"""Offline OIV conversion parity: reviewed packages, batches, and inert plans."""
from __future__ import annotations

import json
from pathlib import Path
import stat
import zipfile

from allin1_sdk.oiv_workbench import OivWorkbench
from allin1_sdk.paths import project_root
from allin1_sdk.release_paths import relative_path, unique_paths
from allin1_sdk.rpf_tools import RpfExplorerService
from allin1_sdk.workspace_desktop import path, digest, file_hash, _inventory


def _source_identity(source):
    if source.is_dir():
        return digest(_inventory(source))
    if not source.is_file() or source.suffix.casefold() not in {".oiv", ".zip"}:
        raise ValueError("Choose an OIV/ZIP or unpacked recipe folder")
    with zipfile.ZipFile(source) as archive:
        infos = archive.infolist()
        if len(infos) > 4000 or sum(item.file_size for item in infos) > 2 * 1024**3:
            raise ValueError("Recipe archive exceeds the bounded inventory")
        seen, files = set(), []
        for info in infos:
            # ZipInfo.filename normalizes Windows separators and truncates NULs.
            # Validate the raw central-directory name before that normalization.
            raw = info.orig_filename
            name = relative_path(raw[:-1] if info.is_dir() else raw).as_posix()
            mode = stat.S_IFMT(info.external_attr >> 16)
            if info.flag_bits & 1 or mode not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ValueError("Encrypted or linked recipe members are not supported")
            if name.casefold() in seen:
                raise ValueError("Duplicate recipe archive destination")
            seen.add(name.casefold())
            if not info.is_dir():
                files.append(name)
        unique_paths(files)
    return file_hash(source)


def _context(payload):
    source = path(payload.get("source"))
    identity = _source_identity(source)
    plan = OivWorkbench().inspect(source)
    if identity != _source_identity(source):
        raise ValueError("Recipe changed during inspection")
    if len(plan.operations) > 256 or len(plan.findings) > 256:
        raise ValueError("Recipe exceeds the desktop limit of 256 operations/findings")
    capabilities = {"managed": plan.managed_exportable,
                    "batches": plan.translatable and not plan.created_archive_operations and all(item.archives for item in plan.operations if item.kind != "archive") and bool(plan.rpf_batch_operations) and not (plan.xml_operations or plan.text_operations or plan.pso_operations) and all(item.supported for item in plan.rpf_batch_operations),
                    "created": plan.translatable and bool(plan.created_archive_operations),
                    "compile": plan.rpf_recipe_compilable}
    return source, plan, identity, capabilities


def inspect(payload):
    source, plan, identity, capabilities = _context(payload)
    return {"source": str(source), "state_sha256": identity, "plan": json.loads(json.dumps(plan.to_dict())), "capabilities": capabilities}


def review(payload):
    source, plan, identity, capabilities = _context(payload)
    if payload.get("expected_state_sha256") != identity:
        raise ValueError("Recipe changed; inspect conversion options again")
    action = payload.get("action")
    if not capabilities.get(action):
        raise ValueError("This recipe does not support the selected conversion; resolve its findings first")
    destination = path(payload.get("destination"), new=True, writable=True)
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise ValueError("Recipe outputs must be separate from their source")
    details = {"action": action, "source": str(source), "destination": str(destination), "state_sha256": identity,
               "outputs": [str(destination)], "operations": json.loads(json.dumps([item for item in plan.to_dict()["operations"]])),
               "archive_write_performed": False, "inert_plan_only": action in {"batches", "compile"}}
    if action in {"created", "compile"}:
        game = path(payload.get("gta_path"))
        if not game.is_dir():
            raise ValueError("Choose a decoder game folder")
        details["edition_context"] = "Enhanced" if (game / "GTA5_Enhanced.exe").is_file() or (game / "eboot.bin").is_file() else "Legacy"
        if details["edition_context"].casefold() not in plan.editions:
            raise ValueError("Decoder context does not match the recipe's target edition")
    if action == "compile":
        archive = path(payload.get("archive"))
        expected = next(item.archives[0] for item in plan.operations if item.kind != "archive" and item.archives)
        if archive.suffix.casefold() != ".rpf" or archive.name.casefold() != expected.replace("\\", "/").split("/")[-1].casefold():
            raise ValueError("Select the exact outer archive named by this recipe")
        before = file_hash(archive)
        service = RpfExplorerService(project_root(), game)
        index = service.index(archive)
        if index.warnings or file_hash(archive) != before:
            raise ValueError("Archive is incomplete or changed during recipe review")
        details.update(archive=str(archive), archive_sha256=before)
    return details


def apply(payload):
    source, plan, identity, _ = _context(payload)
    if identity != payload["expected_state_sha256"]:
        raise ValueError("Recipe changed before conversion")
    output = path(payload["destination"], new=True, writable=True)
    workbench = OivWorkbench()
    action = payload["action"]
    if action == "managed":
        reports = (workbench.export_managed_package(plan, output),)
    elif action == "batches":
        reports = workbench.export_rpf_batch_manifests(plan, output)
    elif action == "created":
        reports = (workbench.export_created_rpf_package(plan, output, project_root=project_root(), gta_path=payload["gta_path"]),)
    elif action == "compile":
        service = RpfExplorerService(project_root(), payload["gta_path"])
        reports = workbench.compile_rpf_recipe_bundle(plan, payload["archive"], output, service=service)
    else:
        raise ValueError("Unknown recipe conversion")
    if _source_identity(source) != identity:
        raise ValueError("Source changed during conversion; output is not qualified and must be reviewed again")
    inventory = _inventory(output)
    return {"output": str(output), "output_sha256": digest(inventory), "file_count": len(inventory),
            "reports": [str(report) for report in reports], "inert_plan_only": action in {"batches", "compile"}, "archive_write_performed": False}
