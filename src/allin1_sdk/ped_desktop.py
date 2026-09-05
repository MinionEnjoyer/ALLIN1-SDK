"""Ped desktop orchestration. The existing copied-workspace domain owns writes."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict
from pathlib import Path

from allin1_sdk.addon_importer import AddonPackageInspector
from allin1_sdk.managed_package_conversion import _safe_publication_path
from allin1_sdk.paths import gta_root_containing
from allin1_sdk.ped_authoring import (
    PED_FIELDS, PedAuthoringWorkspace, _direct_child, _file_sha256,
    _set_preserving_representation,
)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _path(value, *, writable=False):
    if not isinstance(value, str) or not value.strip() or len(value) > 4096 or "\0" in value:
        raise ValueError("A bounded ped source/workspace path is required")
    authored = Path(value).expanduser()
    if not authored.is_absolute():
        raise ValueError("Choose an absolute ped source/workspace path")
    _safe_publication_path(authored)
    path = authored.resolve(strict=True)
    if writable and (not path.is_dir() or gta_root_containing(path)):
        raise ValueError("Ped authoring folders must be outside GTA V")
    return path


def _context(payload):
    if bool(payload.get("workspace")) == bool(payload.get("source")):
        raise ValueError("Choose one ped source or workspace")
    if payload.get("workspace"):
        workspace = PedAuthoringWorkspace(_path(payload["workspace"], writable=True))
        _safe_publication_path(workspace._core.manifest_path)
        _safe_publication_path(workspace.root / workspace.manifest["content_root"])
        _safe_publication_path(workspace.root / "history")
        return workspace.source, workspace
    return _path(payload["source"]), None


def _identifier(payload, key):
    value = payload.get(key)
    if not isinstance(value, str):
        raise ValueError(f"A {key} identifier is required")
    return PedAuthoringWorkspace._validate_identity(value, key)


def _source_digest(source, scan):
    if source.is_file():
        return _file_sha256(source)
    files = []
    for entry in sorted(scan.entries, key=lambda item: item.path):
        path = source / entry.path
        _safe_publication_path(path)
        if not path.resolve(strict=True).is_relative_to(source) or not path.is_file():
            raise ValueError("Unsafe ped source member")
        files.append((entry.path, _file_sha256(path)))
    return _digest(files)


def _assets(scan, ped):
    tokens = {ped.name.casefold(), ped.props_name.casefold(), f"{ped.name.casefold()}_p"} - {""}
    result = []
    for entry in scan.workbench_entries:
        if entry.suffix not in {".ydd", ".ydr", ".ytd", ".ymt", ".ycd", ".meta", ".xml"}:
            continue
        exact = entry.stem.casefold() in tokens
        metadata = entry.path == ped.source
        if metadata or exact or any(len(token) >= 5 and token in entry.name.casefold() for token in tokens):
            role = "Ped metadata" if metadata else {
                ".ydd": "Drawable", ".ydr": "Drawable", ".ytd": "Texture dictionary",
                ".ymt": "YMT metadata (not dependency-resolved)", ".ycd": "Animation dictionary",
            }.get(entry.suffix, entry.category)
            result.append({"path": entry.path, "size": entry.size, "role": role,
                           "link": "definition source" if metadata else "exact identity" if exact else "name candidate",
                           "suffix": entry.suffix, "stem": entry.stem})
    return result


def inspect(payload):
    source, workspace = _context(payload)
    game = _path(payload["gta_path"]) if payload.get("gta_path") else None
    scan = AddonPackageInspector(gta_path=game).inspect(source)
    selected = payload.get("ped")
    if selected is not None:
        selected = _identifier(payload, "ped")
    matches = [ped for ped in scan.peds if not selected or ped.name.casefold() == selected.casefold()]
    if payload.get("metadata_source"):
        matches = [ped for ped in matches if ped.source == payload["metadata_source"]]
    ped = matches[0] if matches else None
    record_index = payload.get("record_index")
    if record_index is not None:
        if (isinstance(record_index, bool) or not isinstance(record_index, int)
                or not 0 <= record_index < len(scan.peds) or scan.peds[record_index] not in matches):
            raise ValueError("Ped record selection changed; refresh the catalog")
        ped = scan.peds[record_index]
    if selected and ped is None:
        raise ValueError("Selected ped definition no longer exists; refresh the catalog")
    unique = bool(ped and sum(item.name.casefold() == ped.name.casefold() for item in scan.peds) == 1)
    values = None
    editable = []
    assets = _assets(scan, ped) if ped else []
    readiness = []
    if ped:
        values = {"ped.pedType": ped.ped_type, "ped.modelType": ped.model_type,
                  "ped.propsName": ped.props_name, "ped.clipDictionary": ped.clip_dictionary,
                  "ped.expressionSet": ped.expression_set, "ped.movementClipSet": ped.movement_clip_set,
                  "ped.creatureMetadata": ped.creature_metadata}
        if workspace and unique:
            item = workspace._record_item(workspace._core.read_tree(ped.source), ped.name)
            editable = [key for key, tag in PED_FIELDS.items() if _direct_child(item, tag) is not None]
        for label, suffixes, identity in (
            ("Drawable", {".ydd", ".ydr"}, ped.name), ("Textures", {".ytd"}, ped.name),
            ("Props", {".ydd", ".ydr", ".ytd"}, ped.props_name or f"{ped.name}_p"),
        ):
            paths = [a["path"] for a in assets if a["suffix"] in suffixes and a["stem"].casefold() == identity.casefold()]
            state = "Present" if paths else "Not found in inspected content"
            if label != "Props" and len(paths) > 1:
                state = "Ambiguous"
            readiness.append({"system": label, "status": state, "evidence": paths or [identity]})
        for label, value in (("Definition", ped.ped_type), ("Movement", ped.movement_clip_set), ("Expressions", ped.expression_set)):
            readiness.append({"system": label, "status": "Declared" if value else "Not declared", "evidence": [value] if value else []})
    can_undo = False
    if workspace:
        try:
            workspace._core.latest_history()
            can_undo = True
        except ValueError:
            pass
    result = {
        "kind": "ped_workbench", "source": str(source), "workspace": str(workspace.root) if workspace else None,
        "revision": workspace.revision if workspace else None,
        "state_sha256": workspace.state_sha256() if workspace else None,
        "project": PedAuthoringWorkspace._project(scan).to_dict(),
        "selected_ped": asdict(ped) if ped else None, "selection_unique": unique,
        "selected_index": record_index if record_index is not None else next((i for i, p in enumerate(scan.peds) if p is ped), None),
        "values": values, "editable_fields": editable, "can_undo": can_undo,
        "can_create": bool(scan.peds and not workspace and scan.source_kind != "rpf"),
        "assets": assets, "readiness": readiness,
        "decoder_edition": scan.inspection_target_edition or scan.edition_tag,
        "read_only": True, "workspace_write_performed": False, "game_write_performed": False,
    }
    _complete(result)
    return result


def _complete(result):
    # Never ask for consent to truncated evidence or silently drop catalog rows.
    from allin1_sdk.desktop_protocol import _bounded
    if _bounded(result) != result:
        raise ValueError("Ped evidence exceeds desktop limits; use a smaller package or the CLI")


def review(payload):
    action = payload.get("action")
    if action == "create":
        source, workspace = _context(payload)
        if workspace:
            raise ValueError("Create requires an original ped source, not a workspace")
        parent = _path(payload.get("parent"), writable=True)
        name = payload.get("name")
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name)
                or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])", name)):
            raise ValueError("Choose a safe workspace name (1–80 letters, digits, underscores or hyphens)")
        destination = parent / name
        _safe_publication_path(destination)
        if destination.exists() or destination == source or destination.is_relative_to(source):
            raise ValueError("Choose a new workspace outside the source")
        scan = AddonPackageInspector().inspect(source)
        if not scan.peds or scan.source_kind == "rpf":
            raise ValueError("Visible peds.meta required; extract direct RPF content before authoring")
        result = {"action": action, "source": str(source), "destination": str(destination),
                  "source_sha256": _source_digest(source, scan), "ped_count": len(scan.peds), "copy_bytes": scan.total_bytes}
    elif action in {"edit", "migrate", "clone", "undo"}:
        source, workspace = _context(payload)
        if workspace is None:
            raise ValueError("Create or open a copied ped workspace before authoring")
        revision = payload.get("expected_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError("A non-negative expected revision is required")
        workspace._check_revision(revision)
        state = workspace.state_sha256()
        if payload.get("expected_state_sha256") != state:
            raise ValueError("Ped snapshot changed; refresh before reviewing edits")
        result = {"action": action, "workspace": str(workspace.root), "revision": revision, "state_sha256": state}
        if action == "clone":
            props = payload.get("new_props")
            if props is not None and not isinstance(props, str):
                raise ValueError("Props identity must be a string")
            plan = workspace.plan_ped_clone(_identifier(payload, "ped"), ped_name=_identifier(payload, "new_name"),
                                            updates={"ped.propsName": props} if props else {})
            result["clone_plan"] = plan.to_dict()
        elif action == "undo":
            history = workspace._core.latest_history()
            workspace._core.verify_post_edit_state(history)
            record = workspace._core.history_record(history)
            result.update({"history_sha256": _digest(record), "subject": record.get("subject", ""),
                           "changes": record.get("changes", []), "renames": record.get("renames", [])})
        else:
            scan = AddonPackageInspector().inspect(source)
            ped = workspace._unique_ped(scan, _identifier(payload, "ped"))
            item = workspace._record_item(workspace._core.read_tree(ped.source), ped.name)
            changes = []
            result.update({"ped": ped.name, "metadata_source": ped.source})
            if action == "edit":
                updates = payload.get("updates")
                if not isinstance(updates, dict) or not updates or set(updates) - set(PED_FIELDS):
                    raise ValueError("Provide existing supported ped fields")
                for key, value in sorted(updates.items()):
                    if not isinstance(value, str):
                        raise ValueError("Ped field values must be strings")
                    normalized = workspace._validate_value(key, value.strip())
                    before, after = _set_preserving_representation(item, PED_FIELDS[key], normalized)
                    if before != after:
                        changes.append({"field": key, "before": before, "after": after})
            else:
                target = _identifier(payload, "new_name")
                props = (_identifier(payload, "new_props") if payload.get("new_props") else
                         f"{target}_p" if ped.props_name.casefold() == f"{ped.name}_p".casefold() else ped.props_name)
                if target.casefold() != ped.name.casefold() and any(p.name.casefold() == target.casefold() for p in scan.peds):
                    raise ValueError("Target ped identity already exists")
                for key, tag, old, value in (("ped.Name", "Name", ped.name, target), ("ped.propsName", "PropsName", ped.props_name, props)):
                    if old.casefold() != value.casefold():
                        before, after = _set_preserving_representation(item, tag, value)
                        changes.append({"field": key, "before": before, "after": after})
                workspace._require_identity_assets(scan, ped.name, ped.props_name, change_props=props.casefold() != ped.props_name.casefold())
                result["renames"] = workspace._identity_asset_renames(scan, ped.name, target, ped.props_name, props)
                result.update({"new_name": target, "new_props": props})
            if not changes:
                raise ValueError("No changed ped fields")
            result["changes"] = changes
        workspace._core.refresh_manifest()
        workspace._check_revision(revision)
        workspace._check_state(state)
    else:
        raise ValueError("Unsupported ped action")
    result.update({"kind": "ped_authoring_review", "review_only": True, "game_write_performed": False})
    result["review_sha256"] = _digest(result)
    _complete(result)
    return result


def apply(payload):
    expected = payload.get("review_sha256")
    if payload.get("authoring_confirmed") is not True or not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Ped writes require a reviewed digest and action-time confirmation")
    current = review(payload)
    if current["review_sha256"] != expected:
        raise ValueError("Ped source, history or request changed after review; review again")
    action = current["action"]
    selected = None
    if action == "create":
        workspace = PedAuthoringWorkspace.create(current["source"], current["destination"])
        # Verify actual copied bytes, including archive sources, before adoption.
        scan = AddonPackageInspector().inspect(workspace.source)
        original_scan = AddonPackageInspector().inspect(current["source"])
        if _source_digest(Path(current["source"]), original_scan) != current["source_sha256"]:
            raise ValueError("Source changed during copy. The new copy is retained for inspection; do not use it without rechecking")
        if Path(current["source"]).is_dir() and _source_digest(workspace.source, scan) != current["source_sha256"]:
            raise ValueError("Copied bytes differ from review. The new copy is retained for inspection")
    else:
        workspace = PedAuthoringWorkspace(current["workspace"])
        kwargs = {"expected_revision": current["revision"], "expected_state_sha256": current["state_sha256"]}
        if action == "clone":
            result = workspace.clone_ped_bundle(current["clone_plan"], expected_revision=current["revision"],
                                                expected_plan_sha256=current["clone_plan"]["plan_sha256"],
                                                expected_state_sha256=current["state_sha256"])
        elif action == "migrate":
            result = workspace.migrate_identity(current["ped"], new_name=current["new_name"], new_props=current["new_props"] or None, **kwargs)
        elif action == "edit":
            result = workspace.update(current["ped"], payload["updates"], **kwargs)
        else:
            result = workspace.undo(**kwargs)
        selected = result.ped
        if action == "undo":
            selected = next((c["before"] for c in result.changes if c["field"] == "ped.Name"), selected)
        if not any(p.name.casefold() == (selected or "").casefold() for p in result.project.peds):
            selected = None
    snapshot = inspect({"workspace": str(workspace.root), **({"ped": selected} if selected else {})})
    snapshot.update({"action": action, "read_only": False, "workspace_write_performed": True})
    return snapshot
