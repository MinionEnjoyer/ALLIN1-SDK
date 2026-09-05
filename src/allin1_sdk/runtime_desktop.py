"""Reviewed native Story controller candidate builds for the React workbench."""
from __future__ import annotations
import json
import hashlib
from pathlib import Path

from allin1_sdk import story_axle_runtime_builder as runtime
from allin1_sdk.axle_prefabs import load_prefab_axle_configuration
from allin1_sdk.axle_runtime_bundler import VehicleAxleBuildInput
from allin1_sdk.release_paths import no_links, strict_json, tree_files
from allin1_sdk.workspace_desktop import path, file_hash, digest


def _source_identity(source):
    if not source.is_dir():
        return {}
    files = {}
    for name in ("src", "include", "tests", "tools", "schemas", "profiles", "examples"):
        folder = source / name
        if folder.is_dir():
            files.update({f"{name}/{key}": file_hash(value) for key, value in tree_files(folder).items()})
    for name in ("CMakeLists.txt", "README.md"):
        item = no_links(source / name)
        if item.is_file():
            files[name] = file_hash(item)
    return files


def _context(payload):
    authored = payload.get("toolchain", {})
    if not isinstance(authored, dict) or set(authored) - {"mode", "cmake_path", "ctest_path", "visual_studio_path"}:
        raise ValueError("Invalid native toolchain choices")
    choices = dict(authored)
    for key in ("cmake_path", "ctest_path", "visual_studio_path"):
        value = choices.get(key)
        if value:
            if not isinstance(value, str) or not Path(value).is_absolute() or ".." in Path(value).parts:
                raise ValueError("Manual toolchain choices must be absolute paths")
            choices[key] = no_links(Path(value))
        else:
            choices[key] = None
    source = no_links(runtime._runtime_source_root())
    identity = _source_identity(source)
    report = runtime.inspect_native_axle_toolchain(source_root=source, settings=runtime.NativeAxleToolchainSettings(**choices))
    if identity != _source_identity(source):
        raise ValueError("Native runtime sources changed during preflight")
    fingerprint = digest({"sources": identity, "toolchain": report.selection_fingerprint, "ready": report.ready})
    return source, report, fingerprint


def inspect(payload):
    source, report, fingerprint = _context(payload)
    return {"source": str(source), "state_sha256": fingerprint, "toolchain": json.loads(json.dumps(report.to_dict())),
            "candidate_only": True, "live_acceptance": "NOT TESTED"}


def _build_request(payload, report):
    if payload.get("action") != "build":
        raise ValueError("Only candidate builds are available in the runtime workbench")
    destination = path(payload.get("destination"), new=True, writable=True)
    targets = payload.get("targets")
    if not isinstance(targets, list) or not targets or len(targets) > 2 or len(set(targets)) != len(targets):
        raise ValueError("Select unique Story Legacy and/or Enhanced targets")
    settings = payload.get("settings", {})
    if not isinstance(settings, dict):
        raise ValueError("Runtime settings must be an object")
    files = payload.get("configuration_files", [])
    if not isinstance(files, list) or len(files) > 32:
        raise ValueError("Choose at most 32 vehicle configuration files")
    inputs, identities = [], {}
    for value in files:
        source = path(value)
        if not source.is_file() or source.stat().st_size > 1024 * 1024:
            raise ValueError("Choose bounded axle configuration JSON files")
        if str(source).casefold() in identities:
            raise ValueError("Duplicate axle configuration file")
        content = source.read_bytes()
        identities[str(source).casefold()] = hashlib.sha256(content).hexdigest()
        configuration = load_prefab_axle_configuration(strict_json(content))
        inputs.append(VehicleAxleBuildInput(configuration=configuration,
            configuration_id=configuration.configuration_id, model_hash=configuration.model_hash,
            minimum_runtime_version=configuration.minimum_runtime_version))
    archive = payload.get("create_archives", True)
    if type(archive) is not bool:
        raise ValueError("Archive selection must be a boolean")
    request = runtime.StoryAxleRuntimeBuildRequest(output_directory=destination, targets=tuple(targets),
        configurations=tuple(inputs), settings=runtime.StoryAxleRuntimeSettings(**settings),
        build_id=payload.get("build_id", "allin1-sdk-local"), create_archives=archive, toolchain_report=report).validate()
    return request, identities


def review(payload):
    source, report, fingerprint = _context(payload)
    if payload.get("expected_state_sha256") != fingerprint:
        raise ValueError("Native source or toolchain changed; run preflight again")
    if not report.ready:
        raise ValueError("Native toolchain is not ready: " + "; ".join(report.problems))
    request, inputs = _build_request(payload, report)
    return {"action": "build", "state_sha256": fingerprint, "source": str(source), "destination": str(request.output_directory),
            "selection_fingerprint": report.selection_fingerprint, "targets": list(request.targets),
            "configuration_sha256": inputs, "settings": request.settings.to_runtime_json(), "build_id": request.build_id,
            "outputs": [str(request.output_directory)], "candidate_only": True, "live_acceptance": "NOT TESTED",
            "toolchain_identity": {"cmake": str(report.cmake_path), "compiler": str(report.cl_path),
                                   "cmake_version": report.cmake_version, "compiler_version": report.cl_version}}


def apply(payload):
    source, report, fingerprint = _context(payload)
    if payload["expected_state_sha256"] != fingerprint:
        raise ValueError("Native build identity changed before execution")
    request, _ = _build_request(payload, report)
    result = runtime.build_story_axle_runtime_candidate(request, source_root=source)
    return {"output": str(result.root), "output_sha256": file_hash(result.manifest),
            "runtime_build": json.loads(json.dumps(result.to_dict())), "candidate_only": True, "live_acceptance": "NOT TESTED"}
