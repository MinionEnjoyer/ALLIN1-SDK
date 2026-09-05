"""Persistent, bounded native-model rendering for the Tauri vehicle viewport."""

from __future__ import annotations

import io
import math
import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from allin1_sdk.addon_importer import PackageAssetContent, PackageAssetReader
from allin1_sdk.asset_preview import PreviewArtifactStore
from allin1_sdk.native_assets import (
    MAX_NATIVE_PREVIEW_BYTES,
    MODEL_PREVIEW_SUFFIXES,
    NativeAssetInspector,
    NativeCollisionScene,
    NativeModelScene,
    native_preview_limit,
)


_MAX_SCENES = 2
_MAX_UV_ATLASES = 12
_VIEWPORT_QUALITIES = frozenset({"interactive", "final"})
_VIEWPORT_MODES = frozenset({
    "materials", "shaded", "textured", "uvs", "wireframe",
})


@dataclass(frozen=True)
class _TextureDictionaryCache:
    public: dict[str, Any]
    images: dict[str, Image.Image]


@dataclass(frozen=True)
class _CollisionDictionaryCache:
    public: dict[str, Any]
    scene: NativeCollisionScene | None


@dataclass(frozen=True)
class _UvAtlasCache:
    public: dict[str, Any]


class VehicleViewportRenderer:
    """Decode validated package members once and render camera-bound PNG frames.

    Package membership is revalidated for every frame. Only the expensive decoded
    scene is retained, keyed by the member digest and decoder context, and the
    cache is deliberately tiny because native scenes can be large.
    """

    def __init__(
        self,
        project_root: str | Path,
        *,
        artifact_root: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve(strict=True)
        configured = artifact_root or os.environ.get("ALLIN1_PREVIEW_DIR", "").strip()
        if not configured:
            raise ValueError("Vehicle viewport artifact cache is unavailable")
        self.artifacts = PreviewArtifactStore(configured)
        self._scenes: OrderedDict[
            tuple[str, str, str], tuple[NativeModelScene, tuple[str, ...]]
        ] = OrderedDict()
        self._texture_dictionaries: OrderedDict[
            tuple[str, str, str], _TextureDictionaryCache
        ] = OrderedDict()
        self._collision_dictionaries: OrderedDict[
            tuple[str, str, str], _CollisionDictionaryCache
        ] = OrderedDict()
        self._uv_atlases: OrderedDict[tuple[str, ...], _UvAtlasCache] = OrderedDict()
        self._lock = threading.RLock()

    @staticmethod
    def _camera_value(value: object, *, name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"Vehicle viewport {name} must be a finite number")
        normalized = float(value)
        if not math.isfinite(normalized):
            raise ValueError(f"Vehicle viewport {name} must be a finite number")
        return normalized

    @staticmethod
    def _selection(value: object, *, name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or "\0" in value or len(value) > 512:
            raise ValueError(f"Vehicle viewport {name} must be a bounded string")
        normalized = value.strip()
        return None if not normalized or normalized.casefold() == "all" else normalized

    def render(
        self,
        source: str | Path,
        entry: str,
        *,
        edition: str = "Enhanced",
        gta_path: str | Path | None = None,
        yaw: float = 34.0,
        pitch: float = 24.0,
        lod: str | None = None,
        component: str | None = None,
        material: str | None = None,
        texture_entry: str | None = None,
        collision_entry: str | None = None,
        collision_visible: bool = False,
        render_mode: str = "shaded",
        quality: str = "final",
    ) -> dict[str, Any]:
        source_path = Path(source).expanduser().resolve(strict=True)
        normalized_edition = str(edition).strip().casefold()
        if normalized_edition not in {"legacy", "enhanced"}:
            raise ValueError("Vehicle viewport edition must be Legacy or Enhanced")
        game_path = (
            Path(gta_path).expanduser().resolve(strict=True)
            if gta_path is not None else None
        )
        if game_path is not None and not game_path.is_dir():
            raise ValueError("Vehicle viewport GTA path must be a directory")
        if not isinstance(entry, str) or not entry.strip() or "\0" in entry:
            raise ValueError("Vehicle viewport requires a package model entry")

        normalized_mode = str(render_mode).strip().casefold()
        if normalized_mode not in _VIEWPORT_MODES:
            raise ValueError(
                "Vehicle viewport mode must be materials, shaded, textured, uvs, or wireframe"
            )
        normalized_quality = str(quality).strip().casefold()
        if normalized_quality not in _VIEWPORT_QUALITIES:
            raise ValueError("Vehicle viewport quality must be interactive or final")
        normalized_yaw = self._camera_value(yaw, name="yaw") % 360.0
        normalized_pitch = min(
            89.0, max(-89.0, self._camera_value(pitch, name="pitch"))
        )
        selected_lod = self._selection(lod, name="LOD")
        selected_component = self._selection(component, name="component")
        selected_material = self._selection(material, name="material")
        selected_texture_entry = self._selection(
            texture_entry, name="texture dictionary",
        )
        selected_collision_entry = self._selection(
            collision_entry, name="collision dictionary",
        )
        if not isinstance(collision_visible, bool):
            raise ValueError("Vehicle viewport collision visibility must be boolean")

        reader = PackageAssetReader(
            source_path, project_root=self.project_root, gta_path=game_path,
        )
        content = reader.read(
            entry, limit=native_preview_limit(entry, MAX_NATIVE_PREVIEW_BYTES),
        )
        suffix = Path(content.path).suffix.casefold()
        if suffix not in MODEL_PREVIEW_SUFFIXES:
            raise ValueError("Vehicle viewport accepts only YFT, YDR, or YDD model assets")
        if content.truncated:
            raise ValueError("Vehicle model exceeds the guarded native preview limit")
        if not content.sha256:
            raise ValueError("Vehicle model could not be digest-bound")

        texture_content: PackageAssetContent | None = None
        if selected_texture_entry is not None:
            if Path(selected_texture_entry).suffix.casefold() != ".ytd":
                raise ValueError("Vehicle viewport texture dictionary must be a YTD asset")
            texture_content = reader.read(
                selected_texture_entry,
                limit=native_preview_limit(
                    selected_texture_entry, MAX_NATIVE_PREVIEW_BYTES,
                ),
            )
            if texture_content.truncated:
                raise ValueError(
                    "Vehicle texture dictionary exceeds the guarded native preview limit"
                )
            if not texture_content.sha256:
                raise ValueError("Vehicle texture dictionary could not be digest-bound")

        collision_content: PackageAssetContent | None = None
        if selected_collision_entry is not None:
            if Path(selected_collision_entry).suffix.casefold() != ".ybn":
                raise ValueError("Vehicle viewport collision dictionary must be a YBN asset")
            collision_content = reader.read(
                selected_collision_entry,
                limit=native_preview_limit(
                    selected_collision_entry, MAX_NATIVE_PREVIEW_BYTES,
                ),
            )
            if collision_content.truncated:
                raise ValueError(
                    "Vehicle collision dictionary exceeds the guarded native preview limit"
                )
            if not collision_content.sha256:
                raise ValueError("Vehicle collision dictionary could not be digest-bound")

        cache_key = (
            content.sha256,
            normalized_edition,
            str(game_path).casefold() if game_path is not None else "",
        )
        with self._lock:
            cached = self._scenes.get(cache_key)
            cache_hit = cached is not None
            if cached is None:
                report = NativeAssetInspector(
                    self.project_root, game_path,
                ).inspect_bytes(
                    content.path, content.data,
                    edition=normalized_edition.title(), truncated=False,
                )
                if report.model_scene is None:
                    detail = next(
                        (item for item in report.warnings if item.strip()),
                        "Native model geometry could not be decoded",
                    )
                    raise ValueError(detail)
                cached = (report.model_scene, tuple(report.warnings))
                self._scenes[cache_key] = cached
                while len(self._scenes) > _MAX_SCENES:
                    self._scenes.popitem(last=False)
            else:
                self._scenes.move_to_end(cache_key)

            scene, warnings = cached
            texture_dictionary: dict[str, Any] | None = None
            texture_images: dict[str, Image.Image] = {}
            if texture_content is not None:
                texture_dictionary, texture_images = self._texture_dictionary(
                    texture_content,
                    edition=normalized_edition,
                    game_path=game_path,
                )
            collision_dictionary: dict[str, Any] | None = None
            collision_scene: NativeCollisionScene | None = None
            if collision_content is not None:
                collision_dictionary, collision_scene = self._collision_dictionary(
                    collision_content,
                    edition=normalized_edition,
                    game_path=game_path,
                )
            encoded, metadata = scene.render(
                yaw=normalized_yaw,
                pitch=normalized_pitch,
                lod=selected_lod,
                component=selected_component,
                material=selected_material,
                render_mode=normalized_mode,
                quality=normalized_quality,
                textures=(
                    texture_images
                    if normalized_mode in {"textured", "uvs"} else None
                ),
                collision_scene=collision_scene,
                collision_visible=collision_visible,
            )
            uv_atlas: dict[str, Any] | None = None
            if normalized_mode == "uvs":
                atlas_key = (
                    content.sha256,
                    texture_content.sha256 if texture_content is not None else "",
                    normalized_edition,
                    str(game_path).casefold() if game_path is not None else "",
                    (selected_lod or "all").casefold(),
                    (selected_component or "all").casefold(),
                    (selected_material or "all").casefold(),
                )
                cached_atlas = self._uv_atlases.get(atlas_key)
                if cached_atlas is not None and Path(
                    str(cached_atlas.public["artifact"]["path"])
                ).is_file():
                    self._uv_atlases.move_to_end(atlas_key)
                    uv_atlas = {**cached_atlas.public, "cache_hit": True}
                else:
                    atlas_png, atlas_metadata = scene.render_uv_atlas(
                        lod=selected_lod,
                        component=selected_component,
                        material=selected_material,
                        textures=texture_images,
                    )
                    atlas_artifact = self.artifacts.write_png(atlas_png)
                    atlas_artifact.update({
                        "width": int(atlas_metadata["width"]),
                        "height": int(atlas_metadata["height"]),
                    })
                    atlas_public = {
                        **atlas_metadata,
                        "artifact": atlas_artifact,
                    }
                    self._uv_atlases[atlas_key] = _UvAtlasCache(public=atlas_public)
                    while len(self._uv_atlases) > _MAX_UV_ATLASES:
                        self._uv_atlases.popitem(last=False)
                    uv_atlas = {**atlas_public, "cache_hit": False}

        viewport_warnings = list(warnings)
        if normalized_mode == "textured":
            if texture_content is None:
                viewport_warnings.append(
                    "Textured preview requires a linked YTD; semantic surface colours were used."
                )
            elif not texture_images:
                viewport_warnings.append(
                    "The linked YTD exposed no bounded preview pixels; semantic surface colours were used."
                )
            elif int(metadata.get("model_render_textured_triangle_count", 0)) == 0:
                viewport_warnings.append(
                    "No selected triangles had both UV0 coordinates and resolved base-colour pixels."
                )
        if uv_atlas is not None:
            if uv_atlas["sampled"]:
                viewport_warnings.append(
                    "UV atlas topology is a deterministic 45,000-triangle sample."
                )
            if int(uv_atlas["seam_triangle_count"]) > 0:
                viewport_warnings.append(
                    "Cross-tile UV seams are counted but not folded into the base-tile atlas."
                )
        if collision_visible and collision_content is None:
            viewport_warnings.append(
                "Collision overlay requires a package-owned YBN dictionary."
            )
        elif collision_visible and (
            collision_scene is None or collision_scene.render_triangle_count == 0
        ):
            viewport_warnings.append(
                "The linked YBN exposed no bounded triangle or box geometry for overlay."
            )

        artifact = self.artifacts.write_png(encoded)
        artifact.update({"width": 960, "height": 680})
        components = [
            {
                "name": item.name,
                "lod": item.lod,
                "geometry_count": item.geometry_count,
                "vertex_count": item.vertex_count,
                "triangle_count": item.triangle_count,
                "material_names": list(item.material_names),
                "texture_names": list(item.texture_names),
            }
            for item in scene.components
        ]
        resolved_textures = (
            {
                str(item.get("name", "")).casefold()
                for item in texture_dictionary.get("textures", [])
            }
            if texture_dictionary is not None else set()
        )
        materials = []
        for material_name in dict.fromkeys(item.name for item in scene.materials):
            records = [item for item in scene.materials if item.name == material_name]
            geometries = [
                geometry for geometry in scene.geometries
                if geometry.material_name.casefold() == material_name.casefold()
            ]
            bindings = list(dict.fromkeys(
                binding for record in records for binding in record.texture_parameters
            ))
            parameter_evidence: dict[
                tuple[str, str, tuple[tuple[float, float, float, float], ...]], int
            ] = {}
            for record in records:
                for parameter in getattr(record, "parameters", ()):
                    key = (
                        parameter.name, parameter.source_type, parameter.values,
                    )
                    parameter_evidence[key] = parameter_evidence.get(key, 0) + 1
            materials.append({
                "index": min(item.index for item in records),
                "name": material_name,
                "record_count": len(records),
                "geometry_count": len(geometries),
                "triangle_count": sum(len(geometry.triangles) for geometry in geometries),
                "lods": sorted({geometry.lod for geometry in geometries}, key=str.casefold),
                "components": sorted(
                    {geometry.component for geometry in geometries}, key=str.casefold,
                ),
                "texture_bindings": [
                    {
                        "slot": slot,
                        "name": name,
                        "resolved": (
                            name.casefold() in resolved_textures
                            if texture_dictionary is not None else None
                        ),
                    }
                    for slot, name in bindings
                ],
                "parameters": [
                    {
                        "name": name,
                        "source_type": source_type,
                        "values": [list(row) for row in values],
                        "record_count": record_count,
                    }
                    for (name, source_type, values), record_count
                    in parameter_evidence.items()
                ],
                "parameter_count": len(parameter_evidence),
            })
        return {
            "kind": "vehicle_model_viewport",
            "source": str(source_path),
            "path": content.path,
            "name": Path(content.path).name,
            "size": content.size,
            "bytes_read": len(content.data),
            "sha256": content.sha256,
            "edition": normalized_edition,
            "artifact": artifact,
            "camera": {
                "yaw": round(normalized_yaw, 2),
                "pitch": round(normalized_pitch, 2),
                "lod": str(metadata.get("model_camera_lod", "All")),
                "component": str(metadata.get("model_camera_component", "All")),
                "material": str(metadata.get("model_camera_material", "All")),
                "render_mode": normalized_mode,
                "quality": normalized_quality,
                "collision_visible": collision_visible,
            },
            "scene": {
                "lods": list(scene.lods),
                "components": components,
                "materials": materials,
                "component_count": len(components),
                "material_count": len(scene.materials),
                "surface_count": len(materials),
                "bone_count": len(scene.bones),
            },
            "metadata": metadata,
            "texture_dictionary": texture_dictionary,
            "collision_dictionary": collision_dictionary,
            "uv_atlas": uv_atlas,
            "warnings": viewport_warnings,
            "cache_hit": cache_hit,
            "read_only": True,
            "workspace_write_performed": False,
            "package_write_performed": False,
            "game_write_performed": False,
        }

    def _texture_dictionary(
        self,
        content: PackageAssetContent,
        *,
        edition: str,
        game_path: Path | None,
    ) -> tuple[dict[str, Any], dict[str, Image.Image]]:
        """Return a cached, bounded catalog for one revalidated linked YTD."""
        assert content.sha256 is not None
        cache_key = (
            content.sha256,
            edition,
            str(game_path).casefold() if game_path is not None else "",
        )
        cached = self._texture_dictionaries.get(cache_key)
        if cached is not None:
            self._texture_dictionaries.move_to_end(cache_key)
            return {**cached.public, "cache_hit": True}, cached.images
        report = NativeAssetInspector(
            self.project_root, game_path,
        ).inspect_bytes(
            content.path, content.data,
            edition=edition.title(), truncated=False,
        )
        artifact = (
            self.artifacts.write_png(report.image_png)
            if report.image_png is not None else None
        )
        texture_images: dict[str, Image.Image] = {}
        textures = []
        for index, item in enumerate(report.texture_previews):
            item_warnings = list(item.warnings)
            thumbnail = getattr(item, "thumbnail_png", None)
            if thumbnail:
                try:
                    with Image.open(io.BytesIO(thumbnail)) as opened:
                        texture_images[item.name.casefold()] = opened.convert("RGB").copy()
                except (OSError, UnidentifiedImageError):
                    item_warnings.append(
                        "Bounded texture pixels could not be decoded for model sampling."
                    )
            textures.append({
                "name": item.name,
                "file_name": item.file_name,
                "width": item.width,
                "height": item.height,
                "mip_levels": item.mip_levels,
                "format": item.format,
                "usage": item.usage,
                "size": item.size,
                "sha256": item.sha256,
                "contact_sheet_index": index,
                "warnings": item_warnings,
            })
        texture_count = int(report.metadata.get("exported_textures", len(textures)))
        public = {
            "path": content.path,
            "name": Path(content.path).name,
            "size": content.size,
            "bytes_read": len(content.data),
            "sha256": content.sha256,
            "texture_count": texture_count,
            "previewed_count": len(textures),
            "truncated": texture_count > len(textures),
            "artifact": artifact,
            "textures": textures,
            "warnings": list(report.warnings),
            "read_only": True,
        }
        cached = _TextureDictionaryCache(public=public, images=texture_images)
        self._texture_dictionaries[cache_key] = cached
        while len(self._texture_dictionaries) > _MAX_SCENES:
            self._texture_dictionaries.popitem(last=False)
        return {**public, "cache_hit": False}, texture_images

    def _collision_dictionary(
        self,
        content: PackageAssetContent,
        *,
        edition: str,
        game_path: Path | None,
    ) -> tuple[dict[str, Any], NativeCollisionScene | None]:
        """Return guarded YBN ownership evidence and its reusable overlay scene."""
        assert content.sha256 is not None
        cache_key = (
            content.sha256,
            edition,
            str(game_path).casefold() if game_path is not None else "",
        )
        cached = self._collision_dictionaries.get(cache_key)
        if cached is not None:
            self._collision_dictionaries.move_to_end(cache_key)
            return {**cached.public, "cache_hit": True}, cached.scene
        report = NativeAssetInspector(
            self.project_root, game_path,
        ).inspect_bytes(
            content.path, content.data,
            edition=edition.title(), truncated=False,
        )
        scene = report.collision_scene
        primitive_counts = (
            dict(scene.primitive_counts) if scene is not None else {}
        )
        warnings = list(report.warnings)
        if scene is None and not warnings:
            warnings.append("No reusable collision geometry was decoded from the YBN.")
        exact_triangles = int(primitive_counts.get("Triangle", 0))
        diagnostic_boxes = int(primitive_counts.get("Box", 0))
        overlay_polygon_count = exact_triangles + diagnostic_boxes
        public = {
            "path": content.path,
            "name": Path(content.path).name,
            "size": content.size,
            "bytes_read": len(content.data),
            "sha256": content.sha256,
            "geometry_count": (
                scene.owner_count if scene is not None
                else int(report.metadata.get("collision_geometry_count", 0))
            ),
            "vertex_count": int(report.metadata.get("collision_vertex_count", 0)),
            "polygon_count": int(report.metadata.get("collision_polygon_count", 0)),
            "material_count": int(report.metadata.get("collision_material_count", 0)),
            "render_triangle_count": (
                scene.render_triangle_count if scene is not None else 0
            ),
            "overlay_polygon_count": overlay_polygon_count,
            "unrendered_polygon_count": max(
                0, sum(primitive_counts.values()) - overlay_polygon_count,
            ),
            "primitive_counts": [
                {
                    "kind": kind,
                    "count": count,
                    "overlay": kind in {"Box", "Triangle"},
                    "fidelity": (
                        "exact mesh" if kind == "Triangle"
                        else "diagnostic hull" if kind == "Box"
                        else "count only"
                    ),
                }
                for kind, count in sorted(primitive_counts.items())
            ],
            "bounds": scene.bounds if scene is not None else None,
            "warnings": warnings,
            "read_only": True,
        }
        cached = _CollisionDictionaryCache(public=public, scene=scene)
        self._collision_dictionaries[cache_key] = cached
        while len(self._collision_dictionaries) > _MAX_SCENES:
            self._collision_dictionaries.popitem(last=False)
        return {**public, "cache_hit": False}, scene
