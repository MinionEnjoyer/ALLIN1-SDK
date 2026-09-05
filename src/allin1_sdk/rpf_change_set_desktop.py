"""Reviewed desktop staging and plan export; never applies an RPF transaction."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from allin1_sdk.gxt2_desktop import _digest, _file_hash
from allin1_sdk.managed_package_conversion import _safe_publication_path
from allin1_sdk.paths import gta_root_containing, project_root
from allin1_sdk.rpf_change_set import RpfChangeSet
from allin1_sdk.rpf_tools import RpfExplorerService

MAX_ACTIONS = 128
MAX_DOCUMENT = 2 * 1024**2
MAX_ARCHIVE = 16 * 1024**3
MAX_PAYLOAD = 512 * 1024**2
SHA = re.compile(r"[a-f0-9]{64}")
FIELDS = {"action", "change_set", "archive", "gta_path", "destination", "expected_sha256",
          "change", "action_id", "position", "authorized_root", "review_sha256", "authoring_confirmed"}


def _path(value, *, new=False, write=False):
    if not isinstance(value, str) or not 0 < len(value) <= 4096 or "\0" in value:
        raise ValueError("Choose a bounded absolute RPF workspace path")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("RPF workspace paths must be absolute")
    _safe_publication_path(path)
    path = path.resolve(strict=not new)
    if write and gta_root_containing(path):
        raise ValueError("Change sets and plans must stay outside GTA V")
    if new:
        if path.exists() or not path.parent.is_dir():
            raise ValueError("Choose a new output with an existing parent folder")
        if (path.suffix.casefold() != ".json" or len(path.name) > 160
                or path.name != path.name.strip() or path.name.endswith('.')
                or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in path.name)
                or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", path.name)):
            raise ValueError("Choose a safe new .json filename")
    return path


def _file(value, limit, *, missing=False):
    if missing and isinstance(value, str) and Path(value).is_absolute() and not Path(value).exists():
        _safe_publication_path(Path(value))
        return Path(value)
    path = _path(value)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("RPF workspace file exceeds the desktop size limit or is not a file")
    return path


def _context(value):
    path = _path(value, write=True)
    _file(str(path), MAX_DOCUMENT)
    data = json.loads(path.read_bytes().decode("utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("archive"), dict) or not isinstance(data.get("actions"), list):
        raise ValueError("Invalid RPF change-set document")
    if len(data["actions"]) > MAX_ACTIONS:
        raise ValueError(f"Desktop change sets are limited to {MAX_ACTIONS} actions")
    archive = _file(data["archive"].get("path"), MAX_ARCHIVE)
    if archive.suffix.casefold() != ".rpf":
        raise ValueError("Change sets must reference a loose RPF archive")
    total = 0
    for item in data["actions"]:
        if isinstance(item, dict) and isinstance(item.get("payload"), dict):
            source = _file(item["payload"].get("path"), MAX_PAYLOAD, missing=True)
            if source == path:
                raise ValueError("The change-set document cannot be its own payload")
            total += source.stat().st_size if source.exists() else 0
    if total > 1024**3:
        raise ValueError("Change-set payloads exceed the 1-GiB desktop total limit")
    state = RpfChangeSet.validate(path)
    # Do not accept a different document after checking its referenced paths.
    if state["payload"] != data:
        raise ValueError("Change set changed during inspection")
    return path, state


def _session(path, state):
    return {"kind": "rpf_change_set_session", "change_set": str(path), "state_sha256": state["change_set_sha256"],
            "archive": state["archive_record"], "actions": list(state["actions"]), "action_limit": MAX_ACTIONS,
            "files_verified": False, "read_only": True, "game_write_performed": False}


def inspect(payload):
    if not isinstance(payload, dict) or set(payload) != {"change_set"}:
        raise ValueError("Inspection requires only a change_set path")
    return _session(*_context(payload["change_set"]))


def _service(archive, game_value, authorized_value=None):
    from allin1_sdk.detector import detect_gta_path
    game = _path(game_value) if game_value else gta_root_containing(archive) or detect_gta_path()
    if game is None or not Path(game).is_dir():
        raise ValueError("Choose a matching GTA V decoding context")
    authorized = _path(authorized_value, write=True) if authorized_value else None
    if authorized is not None and (not authorized.is_dir() or authorized != archive.parent):
        raise ValueError("Authorize only the folder directly containing this workspace archive")
    return RpfExplorerService(project_root(), game, workspace_roots=(authorized,) if authorized else ()), str(game), str(authorized) if authorized else None


def _index(service, archive):
    before = _file_hash(archive)
    index = service.index(archive)
    if (len(index.entries) > 25000 or index.archive_size > MAX_ARCHIVE
            or index.source.resolve() != archive or _file_hash(archive) != before):
        raise ValueError("RPF index changed or exceeds desktop limits")
    return index, {"path": str(archive), "size": index.archive_size, "edition": index.edition, "sha256": before}


def _propose(payload, state):
    actions = copy.deepcopy(list(state["actions"]))
    action = payload["action"]
    if action == "stage":
        change = payload.get("change")
        if (not isinstance(change, dict) or not {"action", "entry"} <= set(change)
                or set(change) - {"action", "archive_path", "entry", "payload", "new_entry"}):
            raise ValueError("Choose one typed RPF change")
        for key in ("entry", "archive_path", "new_entry"):
            value = change.get(key, "" if key == "archive_path" else None)
            if value is not None and (not isinstance(value, str) or len(value) > 2048 or value.startswith(("/", "\\"))
                                      or (key != "archive_path" and "!" in value)):
                raise ValueError("Choose a bounded relative archive/member path")
        if change.get("payload") is not None:
            if _file(change["payload"], MAX_PAYLOAD) == state["change_set"]:
                raise ValueError("The change-set document cannot be its own payload")
        row = RpfChangeSet.prepare_stage(payload["change_set"], **change,
            action_id="change-" + _digest([state["change_set_sha256"], change])[:24])
        actions.append(row)
    elif action in {"remove", "move"}:
        selected = next((i for i, row in enumerate(actions) if row["id"] == payload.get("action_id")), None)
        if selected is None:
            raise ValueError("Selected staged action no longer exists")
        row = actions.pop(selected)
        if action == "move":
            position = payload.get("position")
            if type(position) is not int or not 1 <= position <= len(actions) + 1:
                raise ValueError("Choose an existing one-based action position")
            actions.insert(position - 1, row)
    if len(actions) > MAX_ACTIONS:
        raise ValueError(f"Desktop change sets are limited to {MAX_ACTIONS} actions")
    if sum(row.get("payload", {}).get("size", 0) for row in actions) > 1024**3:
        raise ValueError("Change-set payloads exceed the 1-GiB desktop total limit")
    RpfChangeSet._normalize({**state["payload"], "actions": actions}, verify_files=True)
    return actions


def review(payload):
    if not isinstance(payload, dict) or set(payload) - FIELDS:
        raise ValueError("Unexpected RPF change-set request fields")
    action = payload.get("action")
    common = {"action", "review_sha256", "authoring_confirmed"}
    extra = {"create": {"archive", "gta_path", "destination"},
             "stage": {"change_set", "expected_sha256", "change"},
             "remove": {"change_set", "expected_sha256", "action_id"},
             "move": {"change_set", "expected_sha256", "action_id", "position"},
             "compile": {"change_set", "expected_sha256", "gta_path", "destination", "authorized_root"}}
    if not isinstance(action, str) or action not in extra or set(payload) - common - extra[action]:
        raise ValueError("Unsupported change-set action or fields")
    destination = _path(payload.get("destination"), new=True, write=True) if action in {"create", "compile"} else None
    game, authorized, plan, state_sha, source, before = None, None, None, None, None, []
    if action == "create":
        archive_path = _file(payload.get("archive"), MAX_ARCHIVE)
        if archive_path.suffix.casefold() != ".rpf":
            raise ValueError("Choose a loose RPF archive")
        service, game, _ = _service(archive_path, payload.get("gta_path"))
        _, archive = _index(service, archive_path)
        after = []
    else:
        path, state = _context(payload.get("change_set"))
        if not SHA.fullmatch(str(payload.get("expected_sha256", ""))) or payload["expected_sha256"] != state["change_set_sha256"]:
            raise ValueError("Change set changed after inspection; refresh before reviewing")
        source, state_sha, archive = str(path), state["change_set_sha256"], state["archive_record"]
        before = list(state["actions"])
        after = _propose(payload, state)
        if action == "compile":
            service, game, authorized = _service(state["archive"], payload.get("gta_path"), payload.get("authorized_root"))
            compiled = RpfChangeSet.preview_plan(path, service)
            plan = {k: v for k, v in compiled.items() if k != "created_at"}
        if RpfChangeSet.validate(path)["change_set_sha256"] != state_sha:
            raise ValueError("Change set changed while reviewing")
    request = {k: v for k, v in payload.items() if k not in {"review_sha256", "authoring_confirmed"}}
    result = {"kind": "rpf_change_set_review", "action": action, "request": request, "change_set": source,
              "state_sha256": state_sha, "archive": archive, "gta_path": game, "authorized_root": authorized,
              "destination": str(destination) if destination else None, "before": before, "after": after,
              "plan": plan, "review_only": True, "game_write_performed": False, "archive_write_performed": False}
    # Avoid writing successfully and then discovering the response is too large.
    if len(json.dumps(result, ensure_ascii=True).encode()) > 1024**2:
        raise ValueError("Change-set review exceeds the desktop evidence limit")
    result["review_sha256"] = _digest(result)
    return result


def apply(payload):
    if not isinstance(payload, dict) or payload.get("authoring_confirmed") is not True:
        raise ValueError("Explicit authoring confirmation is required")
    value = review(payload)
    if value["review_sha256"] != payload.get("review_sha256"):
        raise ValueError("Change-set review is stale; review and confirm again")
    action = value["action"]
    if action == "create":
        archive = Path(value["archive"]["path"])
        service, _, _ = _service(archive, value["gta_path"])
        index, metadata = _index(service, archive)
        if metadata != value["archive"]:
            raise ValueError("Source archive changed after review")
        output = _path(value["destination"], new=True, write=True)
        RpfChangeSet.create(index, output, expected_archive_sha256=metadata["sha256"])
        session = inspect({"change_set": str(output)})
    elif action == "compile":
        source = _context(value["change_set"])[0]
        service, _, _ = _service(Path(value["archive"]["path"]), value["gta_path"], value["authorized_root"])
        output = _path(value["destination"], new=True, write=True)
        RpfChangeSet.compile_plan(source, service, output, expected_sha256=value["state_sha256"], expected_plan=value["plan"])
        session = inspect({"change_set": value["change_set"]})
    else:
        source = _context(value["change_set"])[0]
        RpfChangeSet.commit_actions(source, value["after"], expected_sha256=value["state_sha256"])
        output = source
        session = inspect({"change_set": str(source)})
    return {"kind": "rpf_change_set_applied", "action": action, "review_sha256": value["review_sha256"],
            "output": str(output), "output_sha256": _file_hash(output), "session": session,
            "file_write_performed": True, "archive_write_performed": False, "game_write_performed": False,
            "plan_status": value["plan"]["status"] if value["plan"] else None}
