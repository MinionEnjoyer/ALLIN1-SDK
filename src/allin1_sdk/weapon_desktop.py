"""Bounded desktop orchestration for the existing weapon authoring domain."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from allin1_sdk.addon_importer import AddonPackageInspector
from allin1_sdk.paths import gta_root_containing
from allin1_sdk.weapon_authoring import (
    AMMO_FIELDS, COMPONENT_FIELDS, WEAPON_FIELDS, EDITABLE_FIELDS, WeaponAuthoringWorkspace,
    _direct_child, _file_sha256,
)
from allin1_sdk.weapon_camera import ADVANCED_FIELDS, CAMERA_FIELDS, FLAGS_KEY, MAX_FLAGS_LENGTH
from allin1_sdk.weapon_fire_rate import RPM_KEY
from allin1_sdk.weapon_native_preview import native_preview


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _path(payload: dict, key: str, *, writable: bool = False) -> Path:
    raw = payload.get(key)
    if not isinstance(raw, str) or not raw.strip() or "\0" in raw or len(raw) > 4096:
        raise ValueError(f"A valid {key} path is required")
    authored = Path(raw).expanduser()
    if authored.is_symlink() or (writable and gta_root_containing(authored)):
        raise ValueError("Weapon workspaces must be outside GTA V and not symbolic links")
    path = authored.resolve(strict=True)
    if not path.is_dir():
        raise ValueError("Choose an unpacked package folder or an editable workspace")
    return path


def _tree_digest(source: Path) -> tuple[str, Any]:
    scan = AddonPackageInspector().inspect(source)
    hashes = []
    for entry in sorted(scan.entries, key=lambda item: item.path):
        authored = source / entry.path
        path = authored.resolve(strict=True)
        if authored.is_symlink() or not path.is_relative_to(source) or not path.is_file():
            raise ValueError("Weapon source contains an unsafe member")
        hashes.append((entry.path, _file_sha256(path)))
    return _digest(hashes), scan


def inspect(payload: dict) -> dict[str, Any]:
    workspace = WeaponAuthoringWorkspace(_path(payload, "workspace", writable=True)) if payload.get("workspace") else None
    source = workspace.source if workspace else _path(payload, "source")
    scan = AddonPackageInspector().inspect(source)
    project = WeaponAuthoringWorkspace._project(scan)
    editor_kind = payload.get("editor_kind", "weapon")
    if editor_kind not in {"weapon", "component", "attachment", "shop", "animation"}:
        raise ValueError("Unsupported weapon editor kind")
    selected = payload.get("weapon")
    if selected is not None and (not isinstance(selected, str) or len(selected) > 160 or "\0" in selected):
        raise ValueError("Weapon selection must be a bounded identifier")
    if not selected:
        selected = next((w.name for w in project.weapons if sum(x.name.casefold() == w.name.casefold() for x in project.weapons) == 1), None)
    values = None
    editable_fields: list[str] = []
    if selected:
        weapon = project.weapon(selected)
        if workspace:
            values = workspace.values(weapon.name, _scan=scan).to_dict()
            editable_fields.extend(key for key in ADVANCED_FIELDS if key in values["values"])
            if RPM_KEY in values["values"]:
                editable_fields.append(RPM_KEY)
            for kind, fields in (("weapon", WEAPON_FIELDS), ("ammo", AMMO_FIELDS)):
                relative = values["sources"].get(kind)
                if relative:
                    item = workspace._record_item(workspace._core.read_tree(relative), weapon.name if kind == "weapon" else weapon.ammo_info, kind)
                    editable_fields.extend(key for key, tag in fields.items() if _direct_child(item, tag) is not None)
        else:
            values = WeaponAuthoringWorkspace.values_for_scan(scan, weapon.name).to_dict()
        selected = weapon.name
    component_values = None
    attachment_values = None
    shop_values = None
    shop_sources = sorted({record.source for record in project.shop_records
                           if selected and record.weapon_name.casefold() == selected.casefold()})
    relationship_fields: list[str] = []
    if editor_kind == "shop":
        metadata_source = _metadata_source(payload)
        if selected and (metadata_source or len(shop_sources) == 1):
            shop_values = (workspace.shop_values(selected, metadata_source, _scan=scan) if workspace
                           else WeaponAuthoringWorkspace.shop_values_for_scan(scan, selected, metadata_source)).to_dict()
            shop_values["affected_weapons"] = [selected]
            if workspace:
                relationship_fields = [key for key, representation in shop_values["representations"].items()
                                       if representation != "missing"]
    if editor_kind in {"component", "attachment"}:
        component_name = _identifier(payload, "component")
        if editor_kind == "component":
            component_values = WeaponAuthoringWorkspace.component_values_for_scan(scan, component_name).to_dict()
            if workspace:
                item = workspace._record_item(
                    workspace._core.read_tree(component_values["source"]),
                    component_values["component"], "component",
                )
                relationship_fields = [key for key, tag in COMPONENT_FIELDS.items() if _direct_child(item, tag) is not None]
        else:
            weapon_name = _identifier(payload, "weapon")
            links = [link for link in project.attachments if link.weapon_name.casefold() == weapon_name.casefold()
                     and link.component_name.casefold() == component_name.casefold()]
            if len(links) != 1:
                raise ValueError("Weapon attachment link was not found uniquely")
            link = links[0]
            attachment_values = {
                "weapon": link.weapon_name, "component": link.component_name,
                "source": link.source, "affected_weapons": [link.weapon_name],
                "values": {"attachment.attachBone": link.attach_bone, "attachment.default": "true" if link.default else "false"},
                "other_defaults": [other.component_name for other in project.attachments
                    if other.weapon_name.casefold() == link.weapon_name.casefold()
                    and other.attach_bone.casefold() == link.attach_bone.casefold()
                    and other.component_name.casefold() != link.component_name.casefold() and other.default],
            }
            if workspace:
                _point, item, _siblings = workspace._attachment_item(workspace._core.read_tree(link.source), link)
                if _direct_child(item, "Default") is not None:
                    relationship_fields = ["attachment.default"]
    can_undo = False
    if workspace:
        try:
            workspace._core.latest_history()
            can_undo = True
        except ValueError:
            pass
    return {
        "kind": "weapon_workbench", "source": str(source),
        "workspace": str(workspace.root) if workspace else None,
        "revision": workspace.revision if workspace else None,
        "state_sha256": workspace.state_sha256() if workspace else None,
        "project": project.to_dict(), "selected_weapon": selected,
        "values": values, "editable_fields": editable_fields, "can_undo": can_undo,
        "camera_fields": [spec for key, spec in CAMERA_FIELDS.items() if values and key in values["values"]],
        "editor_kind": editor_kind, "component_values": component_values,
        "attachment_values": attachment_values, "relationship_editable_fields": relationship_fields,
        "shop_values": shop_values, "shop_sources": shop_sources,
        "native_preview": native_preview(scan, selected, payload.get("component") if editor_kind in {"component", "attachment"} else None),
        "assets": [{"path": item.path, "size": item.size} for item in scan.entries if item.suffix in {".ydr", ".ydd", ".ytd", ".yft", ".ybn"}][:500],
        "read_only": True, "workspace_write_performed": False, "game_write_performed": False,
    }


def _identifier(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,160}", value):
        raise ValueError(f"A bounded {key} identifier is required")
    return value


def _metadata_source(payload: dict) -> str | None:
    value = payload.get("metadata_source")
    if value is not None and (not isinstance(value, str) or not value or len(value) > 4096 or "\0" in value):
        raise ValueError("Metadata source must be a bounded relative path")
    # The domain resolves this against scanned source records, never an arbitrary file.
    return value


def _clone_spec(payload: dict) -> dict[str, Any]:
    spec = payload.get("spec")
    fields = {"donor_weapon", "weapon_name", "slot", "ammo_info", "model",
              "human_name_hash", "stat_name", "clone_ammo", "ammo_name"}
    if not isinstance(spec, dict) or set(spec) != fields:
        raise ValueError("Weapon clone requires the complete identity and ammo-mode spec")
    if not isinstance(spec["clone_ammo"], bool):
        raise ValueError("clone_ammo must be a boolean")
    for field in fields - {"clone_ammo", "ammo_name"}:
        _identifier(spec, field)
    if spec["ammo_name"] is not None:
        _identifier(spec, "ammo_name")
    return spec


def review(payload: dict) -> dict[str, Any]:
    action = payload.get("action")
    if action == "create":
        source = _path(payload, "source")
        parent = _path(payload, "parent", writable=True)
        name = payload.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", name):
            raise ValueError("Workspace name must use 1–80 letters, numbers, underscores, or hyphens")
        destination = parent / name
        if destination.exists() or destination.is_symlink() or destination == source or destination.is_relative_to(source):
            raise ValueError("Choose a new workspace outside the source folder")
        source_sha256, scan = _tree_digest(source)
        if not scan.weapons:
            raise ValueError("No visible weapons.meta records. Extract opaque RPF content first")
        result = {"action": action, "source": str(source), "destination": str(destination),
                  "source_sha256": source_sha256, "weapon_count": len(scan.weapons), "copy_bytes": scan.total_bytes}
    elif action in {"edit", "edit_component", "edit_attachment", "edit_shop", "clone_animation", "clone", "undo"}:
        workspace = WeaponAuthoringWorkspace(_path(payload, "workspace", writable=True))
        revision = payload.get("expected_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ValueError("A non-negative expected revision is required")
        workspace._check_revision(revision)
        if action == "clone":
            plan = workspace.plan_weapon_clone(**_clone_spec(payload))
            if plan.revision != revision:
                raise ValueError("Weapon workspace revision changed during clone review; review again")
            result = {"action": action, "workspace": str(workspace.root), "revision": revision,
                      "clone_plan": plan.to_dict()}
        elif action == "clone_animation":
            change_review = workspace.review_animation_clone(
                _identifier(payload, "weapon"), _identifier(payload, "template_weapon"), _metadata_source(payload),
            )
            result = {**change_review, "action": action, "domain_review_sha256": change_review["review_sha256"]}
            result.pop("review_sha256")
        elif action != "undo":
            updates = payload.get("updates")
            shared = payload.get("acknowledge_shared", False)
            if not isinstance(shared, bool):
                raise ValueError("Shared-definition acknowledgement must be a boolean")
            if not isinstance(updates, dict) or not 1 <= len(updates) <= len(EDITABLE_FIELDS) or any(
                not isinstance(k, str) or not isinstance(v, str)
                or len(v) > (MAX_FLAGS_LENGTH if k == FLAGS_KEY else 160) or "\0" in v for k, v in updates.items()
            ):
                raise ValueError("Updates must be a bounded set of supported string fields")
            if action == "edit_shop":
                change_review = workspace.review_shop_update(
                    _identifier(payload, "weapon"), updates, _metadata_source(payload),
                )
            elif action == "edit_component":
                change_review = workspace.review_component_update(
                    _identifier(payload, "component"), updates, acknowledge_shared=shared,
                )
            elif action == "edit_attachment":
                change_review = workspace.review_attachment_update(
                    _identifier(payload, "weapon"), _identifier(payload, "component"), updates,
                )
            else:
                change_review = workspace.review_update(_identifier(payload, "weapon"), updates, acknowledge_shared=shared)
            result = {**change_review, "action": action, "domain_review_sha256": change_review["review_sha256"]}
            result.pop("review_sha256")
        else:
            history = workspace._core.latest_history()
            workspace._core.verify_post_edit_state(history)
            record = workspace._core.history_record(history)
            result = {"action": action, "workspace": str(workspace.root), "revision": revision,
                      "state_sha256": workspace.state_sha256(), "history_sha256": _digest(record),
                      "changes": record.get("changes", []), "subject": record.get("subject", "")}
            if record.get("operation") == "weapon_bundle_clone":
                result["removed_records"] = [json.loads(change["after"]) for change in record["changes"]
                                             if change.get("field") == "bundle.created_record"]
    else:
        raise ValueError("Unsupported weapon action; expected create, edit, edit_component, edit_attachment, edit_shop, clone_animation, clone, or undo")
    if action in {"clone", "clone_animation", "edit_shop", "undo"}:
        # A confirmation must describe the complete bundle, not a silently
        # truncated subset of its records or dependencies.
        from allin1_sdk.desktop_protocol import _bounded
        if _bounded(result) != result:
            raise ValueError("Weapon evidence exceeds desktop review limits; use the Tkinter workbench for this operation")
    result.update({"kind": "weapon_authoring_review", "review_only": True, "game_write_performed": False})
    result["review_sha256"] = _digest(result)
    return result


def apply(payload: dict) -> dict[str, Any]:
    expected = payload.get("review_sha256")
    if payload.get("authoring_confirmed") is not True or not isinstance(expected, str) or not re.fullmatch("[0-9a-f]{64}", expected):
        raise ValueError("Weapon authoring requires a reviewed digest and action-time confirmation")
    current = review(payload)
    if current["review_sha256"] != expected:
        raise ValueError("Weapon workspace, source, or edits changed after review; review again")
    action = current["action"]
    if action == "create":
        workspace = WeaponAuthoringWorkspace.create(current["source"], current["destination"])
        if _tree_digest(workspace.source)[0] != current["source_sha256"]:
            # This is the new directory created above, never an existing user workspace.
            if workspace.root != Path(current["destination"]) or workspace.root.is_symlink():
                raise ValueError("Copied workspace target changed; automatic cleanup refused")
            shutil.rmtree(workspace.root)
            raise ValueError("Source changed during copy; the new workspace was discarded")
        selection = {}
    else:
        workspace = WeaponAuthoringWorkspace(current["workspace"])
        if action == "edit":
            result = workspace.update(current["weapon"], payload["updates"],
                expected_revision=current["revision"], acknowledge_shared=payload.get("acknowledge_shared", False),
                expected_review_sha256=current["domain_review_sha256"])
        elif action == "edit_component":
            result = workspace.update_component(
                current["component"], payload["updates"], expected_revision=current["revision"],
                acknowledge_shared=payload.get("acknowledge_shared", False),
                expected_review_sha256=current["domain_review_sha256"],
            )
        elif action == "edit_attachment":
            result = workspace.update_attachment(
                current["weapon"], current["component"], payload["updates"],
                expected_revision=current["revision"], expected_review_sha256=current["domain_review_sha256"],
            )
        elif action == "clone":
            result = workspace.clone_weapon_bundle(
                current["clone_plan"], expected_revision=current["revision"],
                expected_plan_sha256=current["clone_plan"]["plan_sha256"],
            )
        elif action == "edit_shop":
            result = workspace.update_shop(
                current["weapon"], payload["updates"], current["source"],
                expected_revision=current["revision"], expected_review_sha256=current["domain_review_sha256"],
            )
        elif action == "clone_animation":
            result = workspace.clone_animation_mappings(
                current["weapon"], current["template_weapon"], current["source"],
                expected_revision=current["revision"], expected_review_sha256=current["domain_review_sha256"],
            )
        else:
            result = workspace.undo(expected_revision=current["revision"], expected_state_sha256=current["state_sha256"])
        if action == "clone":
            selection = {"weapon": result.subject}
        elif result.subject_kind == "bundle":
            donor = next((change["before"] for change in result.changes if change["field"] == "weapon.Name"), None)
            selection = {"weapon": donor} if donor else {}
        elif result.subject_kind == "component":
            selection = {"editor_kind": "component", "component": result.subject}
        elif result.subject_kind == "attachment":
            weapon, component = result.subject.split("/", 1)
            selection = {"editor_kind": "attachment", "weapon": weapon, "component": component}
        elif result.subject_kind in {"shop", "animation"}:
            selection = {"editor_kind": result.subject_kind, "weapon": result.subject}
            if result.subject_kind == "shop" and result.changes:
                selection["metadata_source"] = result.changes[0].get("source")
        else:
            selection = {"weapon": result.subject} if result.subject_kind == "weapon" else {}
    snapshot = inspect({"workspace": str(workspace.root), **selection})
    snapshot.update({"action": action, "read_only": False, "workspace_write_performed": True})
    return snapshot
