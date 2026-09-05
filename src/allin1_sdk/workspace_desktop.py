"""Reviewed desktop adapters for offline authoring workspaces.

No CLI forwarding, game installation, or arbitrary operation dispatch is exposed.
Inspection/review are read-only; mutations revalidate the exact request and inputs.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

from allin1_sdk.binary_workspace import BinaryPatchWorkspace, MAX_BINARY_WORKSPACE_BYTES, _decode_hex
from allin1_sdk.paths import gta_root_containing, project_root
from allin1_sdk.release_paths import no_links, relative_path, strict_json, tree_files

SCHEMA = 1
MODULES = {"binary", "maps", "graph", "program", "runtime", "render", "recipe", "vehicle_identity", "data_tools", "code"}
_LOCKS: dict[str, threading.RLock] = {}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def path(value, *, new=False, writable=False):
    if not isinstance(value, str) or not value or len(value) > 4096 or "\0" in value:
        raise ValueError("Choose a bounded absolute path")
    authored = Path(value)
    if not authored.is_absolute() or ".." in authored.parts:
        raise ValueError("Choose an absolute path without traversal")
    selected = no_links(authored)
    # Validate Windows names even on other development hosts.
    for part in selected.parts[1:]:
        relative_path(part)
    selected = selected.resolve(strict=not new)
    if writable and gta_root_containing(selected):
        raise ValueError("Authoring workspaces and outputs must be outside GTA V")
    if new and (selected.exists() or not selected.parent.is_dir()):
        raise ValueError("Choose a new destination with an existing parent folder")
    return selected


def _integer(value, label, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise ValueError(f"{label} must be an integer between 0 and {maximum}")
    return value


def _fields(payload, allowed):
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("Unexpected workspace request fields")
    if payload.get("module") not in MODULES:
        raise ValueError("Unknown authoring module")


def _inventory(root, *, limit=4000, size_limit=2 * 1024**3):
    files = tree_files(root)
    if len(files) > limit or sum(p.stat().st_size for p in files.values()) > size_limit:
        raise ValueError("Authoring input exceeds the bounded file inventory")
    return {name: file_hash(p) for name, p in sorted(files.items())}


@contextmanager
def _operation_lock(target):
    # A per-user temporary lock also serializes separate sidecar processes without
    # placing lock files in immutable sources or changing their fingerprints.
    key = hashlib.sha256(str(target).casefold().encode()).hexdigest()
    with _LOCKS.setdefault(key, threading.RLock()):
        lock = no_links(Path(tempfile.gettempdir()) / f"allin1-authoring-{key}.lock")
        with lock.open("a+b") as stream:
            if stream.tell() == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _binary_context(payload):
    if sum(bool(payload.get(key)) for key in ("source", "workspace", "archive")) != 1:
        raise ValueError("Choose one binary source, archive member, or workspace")
    if payload.get("archive"):
        from allin1_sdk.rpf_tools import RpfExplorerService
        archive = path(payload["archive"])
        game = path(payload.get("gta_path"))
        entry_id = payload.get("entry_id")
        if not isinstance(entry_id, str) or len(entry_id) > 2048 or entry_id.count("::") != 1:
            raise ValueError("Choose one exact indexed archive member")
        before = file_hash(archive)
        service = RpfExplorerService(project_root(), game)
        index = service.index(archive)
        try:
            entry = index.entry(entry_id)
        except KeyError as exc:
            raise ValueError("The selected archive member no longer exists") from exc
        if entry.kind == "directory" or not 0 < entry.size <= 128 * 1024**2:
            raise ValueError("Binary archive intake is limited to one nonempty member within 128 MiB")
        with tempfile.TemporaryDirectory(prefix="allin1-binary-intake-") as temporary:
            extracted = service.extract(index, entry, Path(temporary) / "member.bin")
            with extracted.open("rb") as stream:
                data = stream.read(128 * 1024**2 + 1)
        if not data or len(data) > 128 * 1024**2 or file_hash(archive) != before:
            raise ValueError("Archive member changed or exceeds the intake limit")
        binding = {"outer_archive": str(archive), "outer_archive_sha256": before, "entry_id": entry.id,
                   "edition": index.edition, "gta_path": str(game), "extracted_sha256": hashlib.sha256(data).hexdigest(), "name": entry.name}
        return archive, None, digest(binding), data, binding
    if payload.get("workspace"):
        root = path(payload["workspace"], writable=True)
        inventory = _inventory(root, size_limit=2 * MAX_BINARY_WORKSPACE_BYTES + 32 * 1024**2)
        if len(inventory) > 2003:
            raise ValueError("Binary history exceeds the desktop limit")
        if any(any(part.startswith(".") for part in name.split("/")) for name in inventory):
            raise ValueError("Binary workspace contains an unfinished temporary write")
        state = BinaryPatchWorkspace.validate(root)
        return root, state, digest(inventory), None, None
    root = path(payload.get("source"))
    if not root.is_file() or not 0 < root.stat().st_size <= MAX_BINARY_WORKSPACE_BYTES:
        raise ValueError("Choose a nonempty binary file within the 512 MiB limit")
    return root, None, file_hash(root), None, None


def _binary_inspect(payload):
    root, state, fingerprint, archive_data, binding = _binary_context(payload)
    editable = state["editable"] if state else root
    original = state["original"] if state else root
    size = len(archive_data) if archive_data is not None else editable.stat().st_size
    offset = _integer(payload.get("offset", 0), "Offset", size - 1)
    length = _integer(payload.get("length", 256), "Page size", 1024)
    if length == 0:
        raise ValueError("Page size must be positive")
    if archive_data is not None:
        data = baseline = archive_data[offset:offset + length]
    else:
        with editable.open("rb") as stream, original.open("rb") as before:
            stream.seek(offset)
            before.seek(offset)
            data, baseline = stream.read(length), before.read(length)
    records = state["records"] if state else []
    return {
        "source": str(root), "workspace": str(root) if state else None,
        "name": state["manifest"]["name"] if state else binding["name"] if binding else root.name,
        "state_sha256": fingerprint, "size": size, "offset": offset, "length": length,
        "bytes": list(data), "original_bytes": list(baseline), "revision": len(records),
        "original_sha256": state["manifest"]["original_sha256"] if state else binding["extracted_sha256"] if binding else fingerprint,
        "editable_sha256": state["editable_sha256"] if state else binding["extracted_sha256"] if binding else fingerprint,
        "source_binding": state["manifest"].get("source_binding", {}) if state else binding or {},
        "archive": str(root) if binding else None, "entry_id": binding["entry_id"] if binding else None,
        "gta_path": binding["gta_path"] if binding else None,
        "history": [{key: row[key] for key in ("sequence", "offset", "length", "created_utc")}
                    for row in records[-100:]],
    }


def _binary_review(payload):
    root, state, fingerprint, archive_data, binding = _binary_context(payload)
    if payload.get("expected_state_sha256") != fingerprint:
        raise ValueError("Binary input changed; inspect it again before review")
    action = payload.get("action")
    details = {"state_sha256": fingerprint, "source": str(root), "action": action}
    if action == "create" and state is None:
        target = path(payload.get("destination"), new=True, writable=True)
        details.update(destination=str(target), size=len(archive_data) if archive_data is not None else root.stat().st_size,
                       source_binding=binding)
    elif action in {"patch", "undo", "build"} and state:
        if action == "patch":
            if not isinstance(payload.get("expected_hex"), str) or not isinstance(payload.get("replacement_hex"), str):
                raise ValueError("Expected and replacement bytes are required")
            expected = _decode_hex(payload["expected_hex"], "Expected bytes")
            replacement = _decode_hex(payload["replacement_hex"], "Replacement bytes")
            if len(expected) != len(replacement) or not 0 < len(expected) <= 8192:
                raise ValueError("Patch requires equal byte counts, at most 8192 bytes")
            offset = _integer(payload.get("offset"), "Patch offset", state["manifest"]["size"] - len(expected))
            with state["editable"].open("rb") as stream:
                stream.seek(offset)
                if stream.read(len(expected)) != expected:
                    raise ValueError("Expected bytes do not match the current asset")
            if expected == replacement:
                raise ValueError("Patch would not change the asset")
            details.update(offset=offset, before=expected.hex(" "), after=replacement.hex(" "), length=len(expected))
        elif action == "undo":
            if not state["records"]:
                raise ValueError("No binary patch to undo")
            latest = state["records"][-1]
            details.update(offset=latest["offset"], before=latest["new_hex"], after=latest["old_hex"])
        else:
            target = path(payload.get("destination"), new=True, writable=True)
            if target.is_relative_to(root):
                raise ValueError("Build output must be outside the binary workspace")
            path(str(target) + ".binary-diff.json", new=True, writable=True)
            if state["editable_sha256"] == state["manifest"]["original_sha256"]:
                raise ValueError("Binary workspace has no changes to build")
            # Older domain writers use these temporary names. Refuse any existing
            # object before invoking them, including links to outside canaries.
            path(str(target.with_name(f".{target.name}.tmp")), new=True, writable=True)
            report = target.with_name(f"{target.name}.binary-diff.json")
            path(str(report.with_name(f".{report.name}.tmp")), new=True, writable=True)
            details.update(destination=str(target), output_sha256=state["editable_sha256"])
    else:
        raise ValueError("Action is not available for this binary input")
    return details


def _binary_apply(payload):
    action = payload["action"]
    if action == "create":
        source, _, fingerprint, data, binding = _binary_context(payload)
        if data is None:
            with source.open("rb") as stream:
                data = stream.read(MAX_BINARY_WORKSPACE_BYTES + 1)
            fingerprint = hashlib.sha256(data).hexdigest()
        if fingerprint != payload["expected_state_sha256"]:
            raise ValueError("Binary source changed before copying")
        root = BinaryPatchWorkspace().export_bytes(binding["name"] if binding else source.name, data, payload["destination"], source_binding=binding)
    else:
        root = path(payload["workspace"], writable=True)
        if action == "patch":
            BinaryPatchWorkspace.patch(root, payload["offset"], payload["replacement_hex"], expected_hex=payload["expected_hex"])
        elif action == "undo":
            BinaryPatchWorkspace.undo(root)
        else:
            output, report = BinaryPatchWorkspace.build(root, payload["destination"])
            return {"output": str(output), "report": str(report), "output_sha256": file_hash(output)}
    return {"session": inspect({"module": "binary", "workspace": str(root)})}


def _map_context(payload):
    from allin1_sdk.map_contract import MapProject
    descriptor = path(payload.get("descriptor"), writable=True)
    if not descriptor.is_file() or descriptor.stat().st_size > 128 * 1024:
        raise ValueError("Choose a map descriptor within 128 KiB")
    project = MapProject.from_dict(strict_json(descriptor.read_text(encoding="utf-8-sig")))
    return descriptor, project, file_hash(descriptor)


def _map_inspect(payload):
    descriptor, project, fingerprint = _map_context(payload)
    result = {"descriptor": str(descriptor), "document": project.to_dict(), "state_sha256": fingerprint}
    if payload.get("source"):
        from allin1_sdk.map_project import MapProjectResolver
        source = path(payload["source"])
        _inventory(source) if source.is_dir() else file_hash(source)
        game = path(payload["gta_path"]) if payload.get("gta_path") else None
        from allin1_sdk.addon_importer import AddonPackageInspector
        scan = AddonPackageInspector(project_root(), game).inspect(source)
        result["inventory"] = MapProjectResolver.inspect_scan(scan, require_ymap=project.streaming.mode == "ipl").to_dict()
    if payload.get("detect_installed"):
        from allin1_sdk.map_project import MapProjectResolver
        game = path(payload.get("gta_path"))
        ipls = list(project.streaming.ipls) + [ipl for level in project.levels for ipl in level.ipls]
        result["detection"] = MapProjectResolver().detect_installed_dlc(
            project.streaming.pack_name, project_root=project_root(), gta_path=game, expected_ipls=ipls).to_dict()
    return result


def _map_review(payload):
    from allin1_sdk.map_contract import MapProject
    action = payload.get("action")
    if action not in {"create", "save", "build"}:
        raise ValueError("Unknown map action")
    project = MapProject.from_dict(payload.get("document"))
    if action == "create":
        target = path(payload.get("destination"), new=True, writable=True)
        fingerprint = None
    else:
        target, _, fingerprint = _map_context(payload)
        if payload.get("expected_state_sha256") != fingerprint:
            raise ValueError("Map descriptor changed; reload before review")
    details = {"action": action, "destination": str(target), "state_sha256": fingerprint, "document": project.to_dict()}
    if action == "build":
        from allin1_sdk.map_project import MapProjectResolver
        source = path(payload.get("source"))
        output = path(payload.get("destination"), new=True, writable=True)
        if output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError("Map output must be separate from the source")
        edition = payload.get("edition")
        if edition not in {"legacy", "enhanced"} or edition not in project.editions:
            raise ValueError("Choose an edition supported by the map descriptor")
        identity = _inventory(source) if source.is_dir() else file_hash(source)
        game = path(payload["gta_path"]) if payload.get("gta_path") else None
        from allin1_sdk.addon_importer import AddonPackageInspector
        scan = AddonPackageInspector(project_root(), game).inspect(source)
        report = MapProjectResolver.inspect_scan(scan, require_ymap=project.streaming.mode == "ipl")
        if not report.valid:
            raise ValueError("Map source has validation errors; inspect and resolve them first")
        details.update(destination=str(output), source=str(source), source_sha256=digest(identity), edition=edition)
    return details


def _map_apply(payload):
    from allin1_sdk.map_contract import MapProject
    project = MapProject.from_dict(payload["document"])
    if payload["action"] == "build":
        from allin1_sdk.map_package import MapAddonPackageBuilder
        game = path(payload["gta_path"]) if payload.get("gta_path") else None
        result = MapAddonPackageBuilder(project_root(), game).build(
            payload["source"], project, payload["destination"], edition=payload["edition"])
        return {"build": result.to_dict()}
    target = path(payload.get("descriptor") if payload["action"] == "save" else payload.get("destination"),
                  new=payload["action"] == "create", writable=True)
    content = json.dumps(project.to_dict(), indent=2, allow_nan=False) + "\n"
    if payload["action"] == "create":
        with target.open("x", encoding="utf-8") as stream:
            stream.write(content)
    else:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, prefix=".map-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            if file_hash(target) != payload["expected_state_sha256"]:
                raise ValueError("Map descriptor changed before saving")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return {"session": inspect({"module": "maps", "descriptor": str(target)})}


_INSPECT_FIELDS = {"module", "source", "workspace", "descriptor", "offset", "length", "gta_path", "detect_installed", "graph", "template", "source_file", "archive", "entry_id", "toolchain", "blender_executable", "render", "texture_dictionary", "settings", "camera", "edition", "model", "task", "comparison", "document"}
_REVIEW_FIELDS = {"module", "source", "workspace", "descriptor", "action", "destination", "expected_state_sha256",
                  "offset", "expected_hex", "replacement_hex", "document", "edition", "gta_path", "archive", "entry_id", "toolchain", "settings", "targets", "configuration_files", "build_id", "create_archives", "render_id", "node_id", "model", "new_model", "new_handling", "expected_revision", "task", "comparison"}


def _adapter(module, operation):
    if module == "code":
        from allin1_sdk import code_desktop
        return {"inspect": code_desktop.inspect, "review": code_desktop.review, "apply": code_desktop.apply}[operation]
    if module == "data_tools":
        from allin1_sdk import data_tools_desktop
        return {"inspect": data_tools_desktop.inspect, "review": data_tools_desktop.review, "apply": data_tools_desktop.apply}[operation]
    if module == "vehicle_identity":
        from allin1_sdk import vehicle_identity_desktop
        return {"inspect": vehicle_identity_desktop.inspect, "review": vehicle_identity_desktop.review, "apply": vehicle_identity_desktop.apply}[operation]
    if module == "recipe":
        from allin1_sdk import recipe_desktop
        return {"inspect": recipe_desktop.inspect, "review": recipe_desktop.review, "apply": recipe_desktop.apply}[operation]
    if module == "render":
        from allin1_sdk import render_desktop
        return {"inspect": render_desktop.inspect, "review": render_desktop.review, "apply": render_desktop.apply}[operation]
    if module == "runtime":
        from allin1_sdk import runtime_desktop
        return {"inspect": runtime_desktop.inspect, "review": runtime_desktop.review, "apply": runtime_desktop.apply}[operation]
    if module in {"graph", "program"}:
        from allin1_sdk import graph_desktop
        return {"inspect": graph_desktop.inspect, "review": graph_desktop.review, "apply": graph_desktop.apply}[operation]
    return {"binary": {"inspect": _binary_inspect, "review": _binary_review, "apply": _binary_apply},
            "maps": {"inspect": _map_inspect, "review": _map_review, "apply": _map_apply}}[module][operation]


def inspect(payload):
    _fields(payload, _INSPECT_FIELDS)
    result = _adapter(payload["module"], "inspect")(payload)
    return {"kind": "workspace_session", "module": payload["module"], "schema_version": SCHEMA,
            "read_only": True, "game_write_performed": False, **result}


def review(payload):
    _fields(payload, _REVIEW_FIELDS)
    result = _adapter(payload["module"], "review")(payload)
    return {"kind": "workspace_review", "module": payload["module"], "schema_version": SCHEMA,
            "review_only": True, "game_write_performed": False, **result,
            "request_sha256": digest(payload), "review_sha256": digest({"request": payload, "review": result})}


def apply(payload):
    _fields(payload, _REVIEW_FIELDS | {"review_sha256", "authoring_confirmed"})
    if payload.get("authoring_confirmed") is not True:
        raise ValueError("Explicit authoring confirmation is required")
    request = {key: value for key, value in payload.items() if key not in {"review_sha256", "authoring_confirmed"}}
    # Complete validation precedes even the operation-lock file write.
    current = review(request)
    if payload.get("review_sha256") != current["review_sha256"]:
        raise ValueError("Review changed; review the action again")
    target = request.get("workspace") or request.get("descriptor") or request.get("destination") or request.get("source")
    with _operation_lock(target):
        if review(request)["review_sha256"] != current["review_sha256"]:
            raise ValueError("Input changed while waiting for the authoring lock")
        result = _adapter(payload["module"], "apply")(request)
    return {"kind": "workspace_applied", "module": payload["module"], "schema_version": SCHEMA,
            "action": payload["action"], "review_sha256": current["review_sha256"],
            "game_write_performed": False, **result}
