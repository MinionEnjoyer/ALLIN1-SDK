"""Reviewed native XML/dependency workspaces for the React desktop.

Exports and builds use the retained native converter. Originals and game files
are never edited. A successful build means reparse validation, not game acceptance.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import wave
from lxml import etree

from allin1_sdk import code_desktop
from allin1_sdk.native_assets import NativeAssetInspector, NATIVE_XML_IMPORT_SUFFIXES
from allin1_sdk.release_paths import contained, strict_json
from allin1_sdk.workspace_desktop import _binary_context, _inventory, digest, file_hash, path
from allin1_sdk.paths import project_root
from allin1_sdk import native_relationships
from allin1_sdk import collision_primitives
from allin1_sdk import animation_samples
from allin1_sdk import asset_validation


def _context(payload):
    if sum(bool(payload.get(key)) for key in ("source", "workspace", "archive")) != 1:
        raise ValueError("Choose one native source, exact archive member, or workspace")
    game = path(payload["gta_path"]) if payload.get("gta_path") else None
    if game and not game.is_dir():
        raise ValueError("Choose the matching GTA installation directory")
    inspector = NativeAssetInspector(project_root(), game)
    if payload.get("workspace"):
        root = path(payload["workspace"], writable=True)
        inventory = _inventory(root)
        manifest_path = contained(root, "native-workspace.json")
        if manifest_path.stat().st_size > 2 * 1024**2:
            raise ValueError("Native workspace manifest exceeds the desktop limit")
        manifest = strict_json(manifest_path.read_bytes())
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1 or manifest.get("operation") != "native_asset_workspace":
            raise ValueError("Unsupported native workspace manifest")
        source = manifest.get("source", {})
        xml_meta = manifest.get("xml", {})
        if not isinstance(source, dict) or not isinstance(xml_meta, dict):
            raise ValueError("Native source and XML metadata are required")
        name = source.get("name")
        if not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("Invalid native source name")
        if source.get("snapshot") != f"original/{name}" or xml_meta.get("path") != f"edit/{name}.xml":
            raise ValueError("Native workspace paths no longer match its source")
        original = contained(root, source["snapshot"])
        xml = contained(root, xml_meta["path"])
        if not original.is_file() or original.stat().st_size != source.get("size") or file_hash(original) != source.get("sha256"):
            raise ValueError("Native source snapshot was modified")
        if not xml.is_file() or not (root / "edit/assets").is_dir():
            raise ValueError("Native workspace XML or assets are missing")
        edition = inspector._normalize_edition(manifest.get("edition", ""))
        state = digest({"inventory": inventory, "gta_path": str(game) if game else None})
        binding = None
        if "native-origin.json" in inventory:
            origin = contained(root, "native-origin.json")
            if origin.stat().st_size > 16384:
                raise ValueError("Native archive origin exceeds its limit")
            binding = strict_json(origin.read_bytes())
        data = None
    else:
        root, _, source_state, data, binding = _binary_context(payload)
        name = binding["name"] if binding else root.name
        if data is None:
            if root.stat().st_size > 128 * 1024**2:
                raise ValueError("Native input exceeds 128 MiB")
            with root.open("rb") as stream:
                data = stream.read(128 * 1024**2 + 1)
            if len(data) > 128 * 1024**2 or hashlib.sha256(data).hexdigest() != source_state:
                raise ValueError("Native source changed during inspection")
        edition = inspector._normalize_edition(binding["edition"] if binding else payload.get("edition", ""))
        state = digest({"source": source_state, "edition": edition, "gta_path": str(game) if game else None})
        manifest, inventory, xml = None, {}, None
    if Path(name).suffix.casefold() not in NATIVE_XML_IMPORT_SUFFIXES:
        raise ValueError("This format has no supported native XML rebuild path")
    return root, game, inspector, name, edition, state, manifest, inventory, xml, data, binding


def inspect(payload):
    root, game, inspector, name, edition, state, manifest, inventory, xml, data, binding = _context(payload)
    result = {"source": str(root), "workspace": str(root) if manifest else None, "name": name,
              "edition": edition, "gta_path": str(game) if game else None, "state_sha256": state,
              "archive_binding": binding, "dependencies": [], "xml_chunks": [], "xml_editable": False,
              "warnings": [], "live_game_acceptance": "not_tested"}
    if manifest:
        result["xml_path"] = str(xml)
        result["xml_size"] = xml.stat().st_size
        if Path(name).suffix.casefold() in {".ydr", ".ydd", ".yft"}:
            try:
                result["asset_validation"] = _asset_report(context=(root, game, inspector, name, edition, state, manifest, inventory, xml, data, binding))
            except (OSError, ValueError) as exc:
                result["warnings"].append(f"Asset validation unavailable: {exc}")
        if Path(name).suffix.casefold() == ".ycd":
            try:
                if xml.stat().st_size > animation_samples.MAX_XML:
                    raise ValueError("Animation XML exceeds the 16 MiB limit")
                _animation_evidence(xml.read_bytes(), payload, result)
            except ValueError as exc:
                result["warnings"].append(f"Animation view unavailable: {exc}")
        if Path(name).suffix.casefold() == ".ybn":
            from allin1_sdk.native_assets import _collision_scene_from_xml
            scene, _, warning = _collision_scene_from_xml(xml, name)
            result["collision"] = collision_primitives.packet(scene)
            if warning:
                result["warnings"].append(warning)
        if Path(name).suffix.casefold() in native_relationships.SUPPORTED:
            try:
                if xml.stat().st_size > native_relationships.MAX_XML:
                    raise ValueError("Relationship XML exceeds the 16 MiB analysis limit")
                result["relationships"] = native_relationships.analyze(xml.read_bytes(), Path(name).suffix.casefold())
            except (OSError, ValueError, etree.XMLSyntaxError) as exc:
                result["warnings"].append(f"Relationship view unavailable: {exc}")
        result["dependencies"] = [{"path": key.removeprefix("edit/assets/"), "sha256": sha,
                                    "size": (root / key).stat().st_size,
                                    "kind": "audio" if key.casefold().endswith(".wav") else "dependency"}
                                   for key, sha in inventory.items() if key.startswith("edit/assets/")]
        if xml.stat().st_size <= code_desktop.MAX_BYTES:
            try:
                code = code_desktop.inspect({"source": str(xml)})
                result.update(xml_chunks=code["chunks"], xml_editable=True)
            except (UnicodeError, ValueError) as exc:
                result["warnings"].append(str(exc))
        if not result["xml_editable"]:
            result["warnings"].append("XML exceeds the inline editor limits. Edit the exported XML externally, refresh, then review a verified build.")
        if payload.get("document") and Path(name).suffix.casefold() != ".ycd":
            document = payload["document"]
            if not isinstance(document, dict) or set(document) - {"dependency", "channel"}:
                raise ValueError("Choose an exact WAV dependency and optional channel")
            selected = document.get("dependency")
            audio = contained(root / "edit/assets", selected)
            if f"edit/assets/{selected}" not in inventory or audio.suffix.casefold() != ".wav":
                raise ValueError("Choose an exported WAV dependency for playback")
            from allin1_sdk.asset_preview import PreviewArtifactStore, MAX_ARTIFACT_BYTES
            cache = os.environ.get("ALLIN1_PREVIEW_DIR", "").strip()
            if not cache:
                result["warnings"].append("Desktop preview cache is unavailable; export the WAV to listen externally.")
            elif audio.stat().st_size > MAX_ARTIFACT_BYTES:
                result["warnings"].append("WAV exceeds the 20 MiB playback limit; export it to listen externally.")
            else:
                try:
                    with audio.open("rb") as stream:
                        samples = stream.read(MAX_ARTIFACT_BYTES + 1)
                    result["audio"] = PreviewArtifactStore(cache).write_wav(samples, channel=document.get("channel"))
                    result["audio_dependency"] = selected
                except (wave.Error, EOFError, ValueError) as exc:
                    result["warnings"].append(f"Audio playback unavailable: {exc}")
    else:
        report = inspector.inspect_bytes(name, data, edition=edition)
        result["metadata"] = report.metadata
        result["warnings"] = list(report.warnings)
        if report.collision_scene is not None:
            result["collision"] = collision_primitives.packet(report.collision_scene)
        result["preview_chunks"] = [(report.structured_text or "")[i:i+8192] for i in range(0, min(len(report.structured_text or ""), 65536), 8192)]
        result["preview_truncated"] = len(report.structured_text or "") > 65536
        if report.structured_text and Path(name).suffix.casefold() == ".ycd":
            try:
                _animation_evidence(report.structured_text.encode("utf-8"), payload, result)
            except ValueError as exc:
                result["warnings"].append(f"Animation view unavailable: {exc}. Export a complete workspace for analysis.")
        if report.structured_text and Path(name).suffix.casefold() in native_relationships.SUPPORTED:
            try:
                result["relationships"] = native_relationships.analyze(report.structured_text.encode("utf-8"), Path(name).suffix.casefold())
            except (ValueError, etree.XMLSyntaxError) as exc:
                result["warnings"].append(f"Relationship view unavailable: {exc}. Export a complete workspace for analysis.")
    return result


def _asset_report(context):
    root, _, _, name, edition, state, manifest, inventory, xml, _, binding = context
    if not manifest or Path(name).suffix.casefold() not in {".ydr", ".ydd", ".yft"}:
        raise ValueError("Asset validation currently requires an exported YDR/YDD/YFT workspace")
    if xml.stat().st_size > 16 * 1024**2:
        raise ValueError("Asset validation XML exceeds 16 MiB")
    with xml.open("rb") as stream:
        report = asset_validation.analyze(stream.read(16 * 1024**2 + 1), source_identity={
            "name": name, "edition": edition, "original_sha256": manifest["source"]["sha256"],
            "workspace_state_sha256": state, "archive": binding})
    if _inventory(root) != inventory:
        raise ValueError("Workspace changed during asset validation; refresh before using the report")
    return report


def _animation_evidence(data, payload, result):
    document = payload.get("document", {})
    if not isinstance(document, dict) or set(document) - {"animation", "model_xml", "drawable", "lod", "skeleton_xml", "skeleton_drawable"}:
        raise ValueError("Choose one exact animation/clip; unexpected inspection settings")
    result["animation"] = animation_samples.analyze(data, document.get("animation"))
    if document.get("model_xml"):
        from allin1_sdk import animation_model
        try:
            result["animation_model"] = animation_model.inspect(document["model_xml"], document.get("drawable"), document.get("lod"),
                skeleton_xml=document.get("skeleton_xml"), skeleton_drawable=document.get("skeleton_drawable"))
        except (OSError, ValueError) as exc:
            result["warnings"].append(f"Animation model binding unavailable: {exc}")
    elif any(document.get(key) is not None for key in ("drawable", "lod", "skeleton_xml", "skeleton_drawable")):
        raise ValueError("Select a model XML before its drawable or LOD")


def _plan(payload):
    context = _context(payload)
    root, game, inspector, name, edition, state, manifest, inventory, xml, data, binding = context
    if payload.get("expected_state_sha256") != state:
        raise ValueError("Native input changed; refresh and review again")
    action = payload.get("action")
    extra = {}
    if action == "export" and not manifest:
        target = path(payload.get("destination"), new=True, writable=True)
        inspector._require_patcher()
        changes = ["Create a native workspace with an immutable source snapshot, editable XML and dependencies."]
    elif action == "build" and manifest:
        target = path(payload.get("destination"), new=True, writable=True)
        if target.suffix.casefold() != Path(name).suffix.casefold() or target.is_relative_to(root):
            raise ValueError("Choose a new output outside the workspace with the original native extension")
        path(str(target) + ".allin1.json", new=True, writable=True)
        inspector._require_patcher()
        changes = ["Rebuild into a new native file and reparse it before publication.", "Emit a validation receipt; game acceptance remains untested."]
    elif action == "export_validation" and manifest:
        target = path(payload.get("destination"), new=True, writable=True)
        if target.suffix.casefold() != ".json" or target.is_relative_to(root):
            raise ValueError("Export validation to a new JSON file outside the workspace")
        report = _asset_report(context)
        extra = {"asset_validation": report}
        changes = ["Export the exact static asset report with source and validator fingerprints. No asset is modified; runtime proof remains separate."]
    elif action == "plan_replacement" and manifest:
        if not isinstance(binding, dict) or not game:
            raise ValueError("An archive-bound workspace and matching GTA installation are required")
        archive = path(binding.get("outer_archive"))
        if file_hash(archive) != binding.get("outer_archive_sha256"):
            raise ValueError("Source archive changed since workspace export; re-export before planning replacement")
        if manifest["source"]["sha256"] != binding.get("extracted_sha256") or name != binding.get("name") or edition != binding.get("edition"):
            raise ValueError("Workspace source no longer matches its archive provenance")
        from allin1_sdk.rpf_change_set_desktop import _service
        document = payload.get("document", {})
        if not isinstance(document, dict) or set(document) - {"authorized_root"}:
            raise ValueError("Unexpected native replacement plan settings")
        service, _, authorized = _service(archive, str(game), document.get("authorized_root"))
        index = service.index(archive)
        try:
            entry = index.entry(binding.get("entry_id"))
        except KeyError as exc:
            raise ValueError("The original native archive entry is missing") from exc
        if entry.name != name or index.edition != edition:
            raise ValueError("Archive entry or edition does not match the native workspace")
        target = path(payload.get("destination"), new=True, writable=True)
        if target.suffix.casefold() != ".json" or target.is_relative_to(root):
            raise ValueError("Choose a new JSON plan outside the native workspace")
        path(str(target.with_name(target.stem + ".payload")), new=True, writable=True)
        extra = {"archive": str(archive), "entry_id": entry.id, "archive_sha256": binding["outer_archive_sha256"], "authorized_root": authorized}
        changes = [f"Build and reparse a candidate for exactly {entry.id} in {archive}.",
                   "Create an inert replacement plan and its payload; archive execution requires a separate review and confirmation."]
    elif action == "save_xml" and manifest:
        target = xml
        request = {"source": str(xml), "action": "save", "document": payload.get("document")}
        request["expected_state_sha256"] = code_desktop.inspect({"source": str(xml)})["state_sha256"]
        evidence = code_desktop.review(request)
        extra = {"code_request": request, "code_review": evidence}
        changes = evidence["changes"]
    elif action == "export_dependency" and manifest:
        member = payload.get("document", {}).get("dependency")
        source = contained(root / "edit/assets", member)
        if f"edit/assets/{member}" not in inventory:
            raise ValueError("Select an exact exported dependency")
        target = path(payload.get("destination"), new=True, writable=True)
        if target.is_relative_to(root) or target.suffix.casefold() != source.suffix.casefold():
            raise ValueError("Export dependencies outside the workspace with their original extension")
        extra = {"dependency_source": str(source), "output_sha256": file_hash(source)}
        changes = ["Copy the selected dependency without modifying the native workspace."]
    else:
        raise ValueError("Choose export, save_xml, build, plan_replacement, export_validation, or export_dependency for this native input")
    if game and target.is_relative_to(game):
        raise ValueError("Native outputs must be outside the selected GTA installation")
    if action in {"build", "plan_replacement"}:
        from allin1_sdk import artifact_identity
        extra["build"] = artifact_identity.current(resource_root=inspector.project_root)
    return context, target, changes, extra


def review(payload):
    context, target, changes, extra = _plan(payload)
    root, _, _, _, edition, state, _, _, _, _, _ = context
    return {"action": payload["action"], "source": str(root), "destination": str(target),
            "state_sha256": state, "edition": edition, "changes": changes,
            "candidate_only": payload["action"] in {"build", "plan_replacement"}, **extra}


def apply(payload):
    context, target, _, extra = _plan(payload)
    root, game, inspector, name, edition, _, manifest, inventory, _, data, binding = context
    action = payload["action"]
    if action == "export":
        # Retain exact archive provenance for a later reviewed replacement handoff.
        with tempfile.TemporaryDirectory(prefix="allin1-native-export-", dir=target.parent) as temporary:
            staged = Path(temporary) / "workspace"
            inspector.export_workspace_bytes(name, data, staged, edition=edition, source_path=None if binding else root)
            if binding:
                (staged / "native-origin.json").write_text(json.dumps(binding, indent=2), encoding="utf-8")
            _context({"workspace": str(staged), "gta_path": str(game) if game else None})
            path(str(target), new=True, writable=True)
            staged.rename(target)
        workspace = target
        receipt = {"workspace": str(target)}
    elif action == "save_xml":
        receipt = code_desktop.apply(extra["code_request"])
        receipt.pop("session", None)
        workspace = root
    elif action == "export_validation":
        output = json.dumps(extra["asset_validation"], indent=2).encode("utf-8")
        # Stage before publication. A competing output is never replaced.
        staged = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".allin1-validation-", delete=False) as stream:
                staged = Path(stream.name)
                stream.write(output)
                stream.flush()
                os.fsync(stream.fileno())
            path(str(target), new=True, writable=True)
            if _inventory(root) != inventory:
                raise ValueError("Workspace changed during report export")
            os.link(staged, target)
        finally:
            if staged is not None:
                staged.unlink(missing_ok=True)
        receipt = {"output": str(target), "output_sha256": hashlib.sha256(output).hexdigest(), "asset_validation": extra["asset_validation"]}
        workspace = root
    elif action == "export_dependency":
        source = Path(extra["dependency_source"])
        # Exclusive creation never replaces another output.
        with source.open("rb") as original, target.open("xb") as output:
            shutil.copyfileobj(original, output)
            output.flush()
            os.fsync(output.fileno())
        if file_hash(target) != extra["output_sha256"]:
            target.unlink()
            raise ValueError("Dependency changed during export; incomplete output removed")
        receipt = {"output": str(target), "output_sha256": file_hash(target)}
        workspace = root
    elif action == "plan_replacement":
        from allin1_sdk.rpf_change_set_desktop import _service
        service, _, _ = _service(Path(extra["archive"]), str(game), extra["authorized_root"])
        index = service.index(extra["archive"])
        plan, output, report = service.plan_native_workspace_replacement(index, index.entry(extra["entry_id"]), root, target)
        if _inventory(root) != inventory or file_hash(Path(extra["archive"])) != extra["archive_sha256"]:
            plan.unlink()
            output.unlink()
            report.unlink()
            output.parent.rmdir()
            raise ValueError("Workspace or source archive changed during planning; candidate outputs removed")
        document = strict_json(plan.read_bytes())
        receipt = {"plan": str(plan), "output": str(plan), "output_sha256": file_hash(plan), "plan_status": document["status"],
                   "plan_blocking_reasons": document.get("blocking_reasons", []), "validation_report": str(report),
                   "archive_write_performed": False}
        workspace = root
    else:
        output, report = inspector.build_workspace(root, target)
        if _inventory(root) != inventory:
            output.unlink()
            report.unlink()
            raise ValueError("Native workspace changed during build; candidate output removed")
        receipt = {"output": str(output), "output_sha256": file_hash(output), "validation_report": str(report),
                   "validation": strict_json(report.read_bytes())["validation"], "archive_binding": binding}
        workspace = root
    if action in {"build", "plan_replacement"}:
        from allin1_sdk.artifact_contract import validate_manifest
        try:
            if report.stat().st_size > 4 * 1024**2:
                raise ValueError("Native build receipt exceeds the provenance limit")
            artifact = validate_manifest(strict_json(report.read_bytes()).get("artifact"))
            if artifact["build"] != extra["build"] or artifact["outputs"] != {output.name: file_hash(output)}:
                raise ValueError("Native build identity or output changed after review")
        except (ValueError, OSError):
            # Only these newly created candidate files belong to this operation.
            if action == "plan_replacement":
                plan.unlink()
            output.unlink()
            report.unlink()
            if action == "plan_replacement":
                output.parent.rmdir()
            raise
        receipt["provenance"] = {"artifact_id": artifact["artifact_id"],
                                 "build_fingerprint": artifact["build"]["build_fingerprint"],
                                 "build_mode": artifact["build"]["mode"],
                                 "scope": "Exact native rebuild bytes; not a signature or in-game acceptance."}
    from allin1_sdk.workspace_desktop import inspect as inspect_workspace
    receipt["session"] = inspect_workspace({"module": "native", "workspace": str(workspace), "gta_path": str(game) if game else None})
    return receipt
