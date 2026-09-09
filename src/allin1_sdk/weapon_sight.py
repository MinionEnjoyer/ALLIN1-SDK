"""Read-only native packets for the experimental, offline sight bench.

No inferred game pose, ballistic zero, or installation. One exact bundled
component can be inspected through its declared parent/child attachment frames.
Requests reuse the workbench operation (UI/API/CLI) and exact package members.
"""
from __future__ import annotations

from pathlib import Path

from allin1_sdk import animation_model, animation_samples
from allin1_sdk.addon_importer import PackageAssetReader
from allin1_sdk.native_assets import NativeAssetInspector
from allin1_sdk.paths import project_root


def inspect(payload: dict) -> dict:
    from allin1_sdk import weapon_desktop

    action = payload.get("sight_action")
    summary_only = payload.get("summary_only", False)
    if not isinstance(summary_only, bool):
        raise ValueError("summary_only must be a boolean")
    if action not in {"model", "animation"}:
        raise ValueError("Sight inspection supports model or animation")
    if payload.get("calibration_action") is not None:
        raise ValueError("Sight simulation and recorded calibration are separate operations")
    weapon = weapon_desktop._identifier(payload, "weapon")
    base = {key: payload[key] for key in ("workspace", "source") if key in payload}
    snapshot = weapon_desktop.inspect({**base, "weapon": weapon})
    edition = payload.get("edition")
    if edition not in {"legacy", "enhanced"}:
        raise ValueError("Choose an exact Legacy or Enhanced decoder edition")
    if payload.get("expected_revision") != snapshot["revision"]:
        raise ValueError("Workbench revision changed; refresh before loading a sight asset")
    entry = payload.get("entry")
    if not isinstance(entry, str) or not entry or len(entry) > 4096:
        raise ValueError("Choose an exact package member")
    if action == "model":
        body = next((part for part in snapshot["native_preview"]["parts"] if part["kind"] == "weapon"), None)
        permitted = {asset["path"] for asset in body["assets"]} if body else set()
        if entry not in permitted:
            raise ValueError("Sight model must be an exact body asset for the selected weapon")
        if payload.get("lod", "High") not in animation_model.LODS:
            raise ValueError("Choose an explicit model LOD")
    else:
        if entry not in snapshot["animation_assets"]:
            raise ValueError("Choose an exact bundled YCD; external clips are not substituted")
    component = payload.get("component")
    component_snapshot = None
    if component is not None:
        if action != "model":
            raise ValueError("Component assembly is only supported for model inspection")
        component = weapon_desktop._identifier(payload, "component")
        parts = [p for p in snapshot["native_preview"]["parts"] if p["kind"] == "component" and p["name"] == component]
        if len(parts) != 1 or len(parts[0].get("attach_bones", [])) != 1:
            raise ValueError("Choose a component with one declared mount on this weapon")
        component_entry = payload.get("component_entry")
        if not isinstance(component_entry, str) or component_entry not in {a["path"] for a in parts[0]["assets"]}:
            raise ValueError("Choose an exact bundled asset for the selected component")
        component_snapshot = weapon_desktop.inspect({**base, "weapon": weapon, "editor_kind": "component", "component": component})
    elif "component_entry" in payload or "component_drawable" in payload:
        raise ValueError("Choose a component before its model")
    gta_path = weapon_desktop._path(payload, "gta_path") if payload.get("gta_path") else None
    reader = PackageAssetReader(snapshot["source"], project_root=project_root(), gta_path=gta_path)
    content = reader.read(entry, limit=16 * 1024**2)
    if content.truncated or not content.sha256 or not content.data:
        raise ValueError("Sight source is empty, incomplete, or exceeds 16 MiB")
    decoder = NativeAssetInspector(project_root(), gta_path)
    xml = decoder.decode_xml_bytes(
        Path(entry).name, content.data, edition=edition,
        maximum_xml_bytes=animation_model.SIGHT_MAX_XML if action == "model" else animation_model.MAX_XML)
    if action == "model":
        packet = animation_model.analyze(xml, payload.get("drawable"), payload.get("lod", "High"), sight=True)
    else:
        selection = payload.get("selection")
        packet = animation_samples.analyze(xml, selection, inventory_only=selection is None)
    attachment = None
    if component_snapshot is not None:
        component_entry = payload["component_entry"]
        child_content = reader.read(component_entry, limit=16 * 1024**2)
        if child_content.truncated or not child_content.sha256 or not child_content.data:
            raise ValueError("Component source is empty, incomplete, or exceeds 16 MiB")
        child_xml = decoder.decode_xml_bytes(Path(component_entry).name, child_content.data,
            edition=edition, maximum_xml_bytes=animation_model.SIGHT_MAX_XML)
        child = animation_model.analyze(child_xml, payload.get("component_drawable"), payload.get("lod", "High"), sight=True)
        parent_bone = parts[0]["attach_bones"][0]
        child_bone = component_snapshot["component_values"]["values"]["component.attachBone"]
        parent_indices = [b["index"] for b in packet["bones"] if b["name"] == parent_bone]
        child_indices = [b["index"] for b in child["bones"] if b["name"] == child_bone]
        if len(parent_indices) != 1 or len(child_indices) != 1 or not child["meshes"]:
            raise ValueError("Assembly needs complete, explicit drawables and unique declared attachment bones")
        if packet["vertex_count"] + child["vertex_count"] > 120000 or packet["triangle_count"] + child["triangle_count"] > 180000:
            raise ValueError("Sight assembly exceeds 120,000 vertices / 180,000 triangles")
        attachment = {"component": component, "component_type": component_snapshot["component_values"]["values"]["component.type"],
            "entry": component_entry, "native_sha256": child_content.sha256,
            "parent_bone": parent_bone, "child_bone": child_bone, "parent_index": parent_indices[0],
            "child_index": child_indices[0], "packet": child,
            "scope": "Child bind pose follows the sampled parent mount. No independent component animation, detached magazine, hands or game IK."}
        child_after = reader.read(component_entry, limit=16 * 1024**2)
        component_current = weapon_desktop.inspect({**base, "weapon": weapon, "editor_kind": "component", "component": component})
        if child_after.truncated or child_after.sha256 != child_content.sha256 or component_current["component_values"] != component_snapshot["component_values"]:
            raise ValueError("Component source changed during sight inspection; reload it")
    # The reader revalidates membership and bytes after the native conversion.
    after = reader.read(entry, limit=16 * 1024**2)
    current = weapon_desktop.inspect({**base, "weapon": weapon})
    if (after.sha256 != content.sha256 or after.truncated or current["revision"] != snapshot["revision"]
            or current["values"] != snapshot["values"] or current["native_preview"] != snapshot["native_preview"]):
        raise ValueError("Weapon source changed during sight inspection; reload it")
    if summary_only:
        for model in [packet, *([attachment["packet"]] if attachment else [])]:
            if action == "model":
                model.update(bone_count=len(model["bones"]), mesh_count=len(model["meshes"]), bones=[], meshes=[],
                    view_unavailable="Summary only; request geometry for the viewport")
            else:
                model.update(track_count=len(model.get("tracks", [])), tracks=[], times=[])
    return {"kind": "weapon_sight_" + action, "weapon": snapshot["selected_weapon"],
            "source": snapshot["source"], "entry": entry, "native_sha256": content.sha256,
            "edition": edition, "revision": snapshot["revision"], "packet": packet, "attachment": attachment,
            "camera_values": snapshot["values"]["values"],
            "summary_only": summary_only,
            "scope": "Experimental model-space inspection; no GTA camera, animation blending or ballistic equivalence.",
            "read_only": True, "workspace_write_performed": False, "game_write_performed": False}
