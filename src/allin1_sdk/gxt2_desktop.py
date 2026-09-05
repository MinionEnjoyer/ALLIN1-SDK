"""Bounded, reviewed desktop access to the existing GXT2 workspace domain."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from allin1_sdk.gxt2_workspace import Gxt2Workspace, MAX_GXT2_BYTES, _label_hash
from allin1_sdk.managed_package_conversion import _safe_publication_path
from allin1_sdk.paths import gta_root_containing

PAGE_SIZE = 100
TEXT_LIMIT = 16_384
CONTEXT_FIELDS = {"source", "workspace", "archive", "entry_id", "gta_path"}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def _file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _payload(payload, fields):
    if not isinstance(payload, dict) or set(payload) - fields:
        raise ValueError("Unexpected GXT2 request fields")


def _path(value, *, new=False, writable=False):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or "\0" in value:
        raise ValueError("A bounded GXT2 path is required")
    authored = Path(value).expanduser()
    if not authored.is_absolute():
        raise ValueError("Choose an absolute GXT2 path")
    _safe_publication_path(authored)
    path = authored.resolve(strict=not new)
    if writable and gta_root_containing(path):
        raise ValueError("GXT2 workspaces and outputs must be outside GTA V")
    if new:
        if path.exists():
            raise ValueError("Destination already exists; choose a new name")
        if not path.parent.is_dir():
            raise ValueError("Destination parent must already exist")
        if (len(path.name) > 160 or path.name != path.name.strip() or path.name.endswith('.')
                or any(c in '<>:"/\\|?*' or ord(c) < 32 for c in path.name)
                or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", path.name)):
            raise ValueError("Choose a safe destination name")
    return path


def _read_source(payload):
    if not payload.get("archive"):
        root = _path(payload.get("source"))
        if root.suffix.casefold() != ".gxt2" or not root.is_file() or root.stat().st_size > MAX_GXT2_BYTES:
            raise ValueError("Choose a bounded loose .gxt2 dictionary")
        with root.open("rb") as stream:
            data = stream.read(MAX_GXT2_BYTES + 1)
        return root, root.name, data, None
    from allin1_sdk.detector import detect_gta_path
    from allin1_sdk.paths import project_root
    from allin1_sdk.rpf_tools import RpfExplorerService

    root = _path(payload["archive"])
    entry_id = payload.get("entry_id")
    if root.suffix.casefold() != ".rpf" or not root.is_file():
        raise ValueError("Choose a loose .rpf archive")
    if (not isinstance(entry_id, str) or not 0 < len(entry_id) <= 4096
            or any(ord(c) < 32 for c in entry_id) or entry_id.count("::") != 1):
        raise ValueError("Choose one exact indexed RPF entry ID")
    game = payload.get("gta_path")
    game = _path(game) if game is not None else gta_root_containing(root) or detect_gta_path()
    if game is None or not Path(game).is_dir():
        raise ValueError("Choose a matching GTA V folder in Archive inspection before opening game text")
    service = RpfExplorerService(project_root(), game)
    index = service.index(root)
    try:
        entry = index.entry(entry_id)
    except KeyError as exc:
        raise ValueError("The selected GXT2 member no longer exists in this archive") from exc
    data, binding = service.read_gxt2_entry(index, entry)
    binding["gta_path"] = str(game)
    return root, entry.name, data, binding


def _archive_binding(value):
    if not isinstance(value, dict) or "outer_archive" not in value:
        return None
    for field in ("outer_archive", "entry_id", "edition"):
        if not isinstance(value.get(field), str) or not 0 < len(value[field]) <= 4096:
            raise ValueError("Invalid GXT2 archive provenance")
    if not re.fullmatch(r"[a-f0-9]{64}", str(value.get("outer_archive_sha256", ""))):
        raise ValueError("Invalid GXT2 archive hash")
    if "gta_path" in value and (not isinstance(value["gta_path"], str) or len(value["gta_path"]) > 4096):
        raise ValueError("Invalid GXT2 GTA context")
    return {key: value[key] for key in ("outer_archive", "outer_archive_sha256", "entry_id", "edition", "gta_path") if key in value}


def _context(payload):
    if sum(payload.get(key) is not None for key in ("workspace", "source", "archive")) != 1:
        raise ValueError("Choose one GXT2 source, archive member, or workspace")
    if not payload.get("archive") and ({"entry_id", "gta_path"} & payload.keys()):
        raise ValueError("RPF entry and GTA context require an archive source")
    workspace = payload.get("workspace")
    if not workspace:
        root, name, data, binding = _read_source(payload)
        entries = Gxt2Workspace.parse(data)
        state = {"name": name, "source_sha256": hashlib.sha256(data).hexdigest(), "revision": 0,
                 "source_binding": binding}
        return root, entries, state, []
    root = _path(workspace, writable=True)
    if not root.is_dir():
        raise ValueError("Choose a GXT2 workspace folder")
    # Bound all reads before the shared validator parses JSON/history.
    files = [root / name for name in ("original.gxt2", "entries.json", "gxt2-workspace.json")]
    history = root / "history"
    _safe_publication_path(history)
    if not history.is_dir():
        raise ValueError("GXT2 history folder is missing")
    for index, path in enumerate(history.iterdir()):
        if index >= 2000:
            raise ValueError("This workspace exceeds the desktop history limit")
        files.append(path)
    total = 0
    for path in files:
        _safe_publication_path(path)
        if not path.is_file():
            raise ValueError("GXT2 workspace contains an unsafe member")
        size = path.stat().st_size
        total += size
        if size > MAX_GXT2_BYTES or total > 256 * 1024 * 1024:
            raise ValueError("This workspace exceeds the desktop byte limit")
    state = Gxt2Workspace.validate(root)
    records = sorted(path for path in files if path.parent == history and not path.name.endswith(".before.json"))
    evidence = [{"sequence": row["sequence"], "action": row["action"], "created_utc": row["created_utc"]}
                for row in (json.loads(path.read_text(encoding="utf-8")) for path in records[-20:])]
    snapshot = {"name": str(state["manifest"]["name"]), "source_sha256": state["manifest"]["original_sha256"],
                "source_binding": _archive_binding(state["manifest"].get("source_binding")),
                "revision": len(records), "files": [(p.relative_to(root).as_posix(), _file_hash(p)) for p in sorted(files)]}
    return root, state["entries"], snapshot, evidence


def inspect(payload):
    _payload(payload, CONTEXT_FIELDS | {"query", "offset", "selected_hash"})
    root, entries, state, history = _context(payload)
    query, offset = payload.get("query", ""), payload.get("offset", 0)
    if not isinstance(query, str) or len(query) > 256 or type(offset) is not int or offset < 0:
        raise ValueError("Invalid GXT2 search or page")
    needle = query.casefold()
    matches = [e for e in entries if needle in e["text"].casefold() or needle in e["hash_hex"].casefold() or needle in str(e["hash"])]
    page = matches[offset:offset + PAGE_SIZE]
    wanted = _label_hash(payload["selected_hash"]) if payload.get("selected_hash") is not None else (page[0]["hash"] if page else None)
    selected = next((dict(e) for e in entries if e["hash"] == wanted), None)
    if selected:
        selected["editable"] = len(selected["text"]) <= TEXT_LIMIT
        selected["text_length"] = len(selected["text"])
        if not selected["editable"]:
            selected["text"] = None
    return {"kind": "gxt2_session", "workspace": str(root) if payload.get("workspace") else None,
            "source": str(root), "name": state["name"], "state_sha256": _digest(state),
            "source_binding": state["source_binding"],
            "original_sha256": state["source_sha256"], "revision": state["revision"], "can_undo": state["revision"] > 0,
            "entry_count": len(entries), "match_count": len(matches), "offset": offset, "page_size": PAGE_SIZE,
            "query": query, "entries": [{"hash": e["hash"], "hash_hex": e["hash_hex"], "preview": e["text"][:180]} for e in page],
            "selected": selected, "history": history, "read_only": True, "game_write_performed": False}


def review(payload):
    _payload(payload, CONTEXT_FIELDS | {"action", "destination", "label_hash", "text", "expected_state_sha256", "review_sha256", "authoring_confirmed", "source_package", "package_metadata", "publication_mode"})
    root, entries, state, _ = _context(payload)
    digest = _digest(state)
    if payload.get("expected_state_sha256") != digest:
        raise ValueError("GXT2 state changed; reopen or refresh before reviewing")
    action = payload.get("action")
    if action not in {"create", "edit", "add", "remove", "undo", "build", "package_rpf", "publish_rpf"}:
        raise ValueError("Unsupported GXT2 action")
    if action != "publish_rpf" and {"source_package", "package_metadata", "publication_mode"} & payload.keys():
        raise ValueError("Package publication fields are only valid for publish_rpf")
    if (action == "create") == bool(payload.get("workspace")):
        raise ValueError("Create an editable workspace before changing or building text")
    if action in {"edit", "add", "remove", "undo"} and state["revision"] >= 1000:
        raise ValueError("Desktop history limit reached; build and open a fresh copied dictionary")
    destination, before, after, label = None, None, None, None
    output_hash = None
    rpf_package = None
    rpf_publication = None
    if action in {"create", "build", "package_rpf", "publish_rpf"}:
        destination = _path(payload.get("destination"), new=True, writable=True)
        if destination.is_relative_to(root):
            raise ValueError("Choose a destination outside the source workspace")
        if action == "build":
            if destination.suffix.casefold() != ".gxt2":
                raise ValueError("Build output must use .gxt2")
            _path(str(destination) + ".gxt2-validation.json", new=True, writable=True)
            output_hash = hashlib.sha256(Gxt2Workspace.encode(entries)).hexdigest()
        elif action == "package_rpf":
            from allin1_sdk.gxt2_rpf_package import review as review_package
            rpf_package = review_package(root, entries, state, destination)
        elif action == "publish_rpf":
            from allin1_sdk.rpf_package_publication import review as review_publication
            rpf_publication = review_publication(root, entries, state, payload.get("source_package"), payload.get("package_metadata"), destination, payload.get("publication_mode", "whole_archive"))
    elif action == "undo":
        if not state["revision"]:
            raise ValueError("No GXT2 edit to undo")
    else:
        value = payload.get("label_hash")
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError("Label hash must be a decimal or hexadecimal unsigned integer")
        label = _label_hash(value)
        existing = next((e for e in entries if e["hash"] == label), None)
        if (action == "add" and existing) or (action != "add" and existing is None):
            raise ValueError("Label already exists" if existing else "Label does not exist")
        before = existing["text"] if existing else None
        if before is not None and len(before) > TEXT_LIMIT:
            raise ValueError("This label exceeds the desktop text limit; it cannot be edited or removed here")
        if action != "remove":
            after = payload.get("text")
            if not isinstance(after, str) or len(after) > TEXT_LIMIT or "\0" in after:
                raise ValueError("Text must be bounded UTF-8 text without NUL characters")
            Gxt2Workspace._validate_entries(({"hash": label, "text": after},))
            if before == after:
                raise ValueError("The text is unchanged")
    result = {"kind": "gxt2_review", "action": action, "source": str(root), "state_sha256": digest,
              "original_sha256": state["source_sha256"],
              "source_binding": state["source_binding"],
              "destination": str(destination) if destination else None, "revision": state["revision"],
              "entry_count": len(entries), "label_hash": label, "before": before, "after": after,
              "output_sha256": output_hash, "review_only": True, "game_write_performed": False}
    if rpf_package is not None:
        result["rpf_package"] = rpf_package
    if rpf_publication is not None:
        result["rpf_publication"] = rpf_publication
    result["review_sha256"] = _digest(result)
    return result


def apply(payload):
    if not isinstance(payload, dict) or payload.get("authoring_confirmed") is not True:
        raise ValueError("GXT2 changes require explicit action-time confirmation")
    initial = review(payload)
    if initial["review_sha256"] != payload.get("review_sha256"):
        raise ValueError("GXT2 review changed; review the action again")
    if payload.get("workspace"):
        workspace = _path(payload["workspace"], writable=True)
        with Gxt2Workspace.operation_lock(workspace):
            return _apply_locked(payload)
    return _apply_locked(payload)


def _apply_locked(payload):
    current = review(payload)
    if current["review_sha256"] != payload.get("review_sha256"):
        raise ValueError("GXT2 review changed; review the action again")
    source = Path(current["source"])
    action = current["action"]
    if action == "create":
        _, name, data, binding = _read_source(payload)
        # Bind the bytes handed to the shared writer, not only the earlier read.
        if (hashlib.sha256(data).hexdigest() != current["original_sha256"]
                or binding != current["source_binding"]):
            raise ValueError("GXT2 source changed while reading")
        Gxt2Workspace().export_bytes(name, data, current["destination"], source_binding=binding or {"source": str(source)})
        session = inspect({"workspace": current["destination"]})
    elif action in {"package_rpf", "publish_rpf"}:
        if action == "package_rpf":
            from allin1_sdk.gxt2_rpf_package import build as build_package
        else:
            from allin1_sdk.rpf_package_publication import build as build_package
        root, entries, state, _ = _context(payload)
        if _digest(state) != current["state_sha256"]:
            raise ValueError("Text workspace changed before RPF packaging")
        return build_package(root, entries, state, Path(current["destination"]), current["rpf_package" if action == "package_rpf" else "rpf_publication"], current["review_sha256"])
    elif action == "build":
        asset, report = Gxt2Workspace.build(source, current["destination"])
        if _file_hash(asset) != current["output_sha256"]:
            raise ValueError("Built GXT2 does not match reviewed output; inspect the destination")
        return {"kind": "gxt2_built", "archive": str(asset), "report": str(report), "sha256": _file_hash(asset),
                "review_sha256": current["review_sha256"], "file_write_performed": True, "game_write_performed": False}
    else:
        if action == "undo":
            Gxt2Workspace.undo(source)
        elif action == "remove":
            Gxt2Workspace.remove(source, current["label_hash"])
        else:
            handler = Gxt2Workspace.add if action == "add" else Gxt2Workspace.set_text
            handler(source, current["label_hash"], current["after"])
        session = inspect({"workspace": str(source), **({"selected_hash": current["label_hash"]} if action in {"edit", "add"} else {})})
    return {"kind": "gxt2_applied", "action": action, "session": session, "review_sha256": current["review_sha256"],
            "file_write_performed": True, "game_write_performed": False}
