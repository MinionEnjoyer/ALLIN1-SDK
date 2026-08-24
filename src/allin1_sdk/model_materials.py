"""Inspection and guarded authoring for native model material bindings.

This module deliberately edits only XML produced by the SDK's native-asset
round trip.  It never patches a game installation or an opaque RAGE resource
in place.  Builds remain delegated to :class:`NativeAssetInspector`, which
reparses every compiled result before publication.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from lxml import etree

from allin1_sdk.authoring_core import GuardedXmlWorkspace
from allin1_sdk.native_assets import (
    MAX_MODEL_XML_BYTES,
    MODEL_PREVIEW_SUFFIXES,
    NATIVE_WORKSPACE_SCHEMA,
    NativeAssetInspector,
    NativeAssetReport,
    NativeModelScene,
    load_native_model_scene,
    resolve_shader_name,
)


MATERIAL_WORKSPACE_SCHEMA = 1
MATERIAL_PROJECT_SCHEMA = 1
MATERIAL_MANIFEST_NAME = "material-workbench.json"
MATERIAL_WORKSPACE_OPERATION = "model_material_workspace"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _local_name(element: etree._Element) -> str:
    return etree.QName(element).localname


def _direct_child(parent: etree._Element, name: str) -> etree._Element | None:
    return next((
        child for child in parent
        if isinstance(child.tag, str) and _local_name(child) == name
    ), None)


def _element_value(element: etree._Element | None) -> str:
    if element is None:
        return ""
    if "ref" in element.attrib:
        return element.attrib["ref"].strip()
    if "value" in element.attrib:
        return element.attrib["value"].strip()
    return (element.text or "").strip()


def _set_value(element: etree._Element, value: str) -> None:
    if "ref" in element.attrib:
        element.attrib.pop("value", None)
        element.set("ref", value)
        element.text = None
    elif "value" in element.attrib:
        element.attrib.pop("ref", None)
        element.set("value", value)
        element.text = None
    else:
        element.attrib.pop("ref", None)
        element.attrib.pop("value", None)
        element.text = value


def _normalize_edition(value: str) -> str:
    normalized = str(value).strip().casefold()
    if normalized in {"enhanced", "gen9"}:
        return "Enhanced"
    if normalized == "legacy":
        return "Legacy"
    raise ValueError("Model material edition must be Legacy or Enhanced")


def _validate_text(value: str, label: str, *, allow_empty: bool = False) -> str:
    normalized = str(value).strip()
    if (not normalized and not allow_empty) or len(normalized) > 160:
        requirement = "0–160" if allow_empty else "1–160"
        raise ValueError(f"{label} must contain {requirement} characters")
    if any(ord(character) < 32 for character in normalized):
        raise ValueError(f"{label} contains control characters")
    return normalized


def _read_tree(path: Path) -> etree._ElementTree:
    size = path.stat().st_size
    if not 0 < size <= MAX_MODEL_XML_BYTES:
        raise ValueError("Model XML is empty or exceeds the guarded 192 MiB limit")
    with path.open("rb") as stream:
        prefix = stream.read(65_536).upper()
    if b"<!DOCTYPE" in prefix or b"<!ENTITY" in prefix:
        raise ValueError("Model XML contains a prohibited DTD or entity declaration")
    parser = etree.XMLParser(
        resolve_entities=False, no_network=True, load_dtd=False,
        recover=False, huge_tree=True, remove_blank_text=False,
        remove_comments=False,
    )
    try:
        tree = etree.parse(str(path), parser)
    except (OSError, etree.XMLSyntaxError) as exc:
        raise ValueError(f"Invalid model XML: {exc}") from exc
    if tree.docinfo.doctype:
        raise ValueError("Model XML contains a prohibited document type")
    return tree


def _shader_items(root: etree._Element) -> tuple[etree._Element, ...]:
    return tuple(root.xpath(
        ".//*[local-name()='ShaderGroup']/*[local-name()='Shaders']"
        "/*[local-name()='Item']"
    ))


def _geometry_items(root: etree._Element) -> tuple[etree._Element, ...]:
    result: list[etree._Element] = []
    seen: set[int] = set()
    for buffer in root.xpath(".//*[local-name()='VertexBuffer']"):
        geometry = buffer.getparent()
        if geometry is not None and id(geometry) not in seen:
            result.append(geometry)
            seen.add(id(geometry))
    return tuple(result)


def _shader_catalog_for_geometry(
    geometry: etree._Element, root: etree._Element,
) -> tuple[etree._Element, ...]:
    ancestor: etree._Element | None = geometry
    while ancestor is not None:
        shaders = tuple(ancestor.xpath(
            "./*[local-name()='ShaderGroup']/*[local-name()='Shaders']"
            "/*[local-name()='Item']"
        ))
        if shaders:
            return shaders
        ancestor = ancestor.getparent()
    if _local_name(root) == "Fragment":
        return _shader_items(root)
    return ()


def _material_parameters(
    shader: etree._Element,
) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    for parameter in shader.xpath(
        "./*[local-name()='Parameters']/*[local-name()='Item']"
    ):
        if parameter.get("type", "").casefold() != "texture":
            continue
        slot = parameter.get("name", "").strip()
        name = _element_value(_direct_child(parameter, "Name"))
        if slot:
            values.append((slot, name))
    return tuple(values)


def _texture_role(slot: str) -> str:
    folded = slot.casefold()
    if any(token in folded for token in ("normal", "bump")):
        return "normal"
    if any(token in folded for token in ("spec", "rough", "metal", "detail")):
        return "surface"
    if any(token in folded for token in ("emiss", "illum", "light")):
        return "emissive"
    if any(token in folded for token in ("diff", "base", "albedo", "colour", "color")):
        return "color"
    return "other"


def _context_name(geometry: etree._Element, ordinal: int) -> tuple[str, str]:
    lod = "Unknown"
    component = f"Geometry {ordinal + 1}"
    ancestor = geometry.getparent()
    while ancestor is not None:
        tag = _local_name(ancestor)
        if tag in {"DrawableModelsHigh", "DrawableModelsMedium", "DrawableModelsLow", "DrawableModelsVeryLow"}:
            lod = tag.removeprefix("DrawableModels") or "High"
        name = _element_value(_direct_child(ancestor, "Name"))
        if name and component.startswith("Geometry "):
            component = name
        ancestor = ancestor.getparent()
    return component, lod


@dataclass(frozen=True)
class MaterialTextureBinding:
    slot: str
    texture: str
    role: str


@dataclass(frozen=True)
class ModelMaterialRecord:
    index: int
    shader: str
    textures: tuple[MaterialTextureBinding, ...]
    geometry_indices: tuple[int, ...] = ()


@dataclass(frozen=True)
class ModelGeometryRecord:
    index: int
    component: str
    lod: str
    material_index: int | None
    material_document_index: int | None
    material_name: str
    available_materials: tuple[str, ...]


@dataclass(frozen=True)
class ModelMaterialFinding:
    severity: str
    code: str
    message: str
    subject: str = ""


@dataclass(frozen=True)
class ModelMaterialProject:
    source: str
    name: str
    suffix: str
    edition: str
    size: int
    sha256: str
    materials: tuple[ModelMaterialRecord, ...]
    geometries: tuple[ModelGeometryRecord, ...]
    components: tuple[dict[str, Any], ...]
    lods: tuple[str, ...]
    metadata: dict[str, Any]
    findings: tuple[ModelMaterialFinding, ...]
    revision: int | None = None
    scene: NativeModelScene | None = field(
        default=None, repr=False, compare=False,
    )

    @property
    def error_count(self) -> int:
        return sum(item.severity == "error" for item in self.findings)

    @property
    def warning_count(self) -> int:
        return sum(item.severity == "warning" for item in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MATERIAL_PROJECT_SCHEMA,
            "operation": "inspect_model_materials",
            "source": self.source,
            "name": self.name,
            "suffix": self.suffix,
            "edition": self.edition,
            "size": self.size,
            "sha256": self.sha256,
            "revision": self.revision,
            "summary": {
                "materials": len(self.materials),
                "texture_bindings": sum(len(item.textures) for item in self.materials),
                "geometries": len(self.geometries),
                "components": len(self.components),
                "errors": self.error_count,
                "warnings": self.warning_count,
            },
            "materials": [asdict(item) for item in self.materials],
            "geometries": [asdict(item) for item in self.geometries],
            "components": list(self.components),
            "lods": list(self.lods),
            "metadata": self.metadata,
            "findings": [asdict(item) for item in self.findings],
        }


@dataclass(frozen=True)
class MaterialAuthoringResult:
    workspace: Path
    revision: int
    subject: str
    changes: tuple[dict[str, str], ...]
    history: Path
    project: ModelMaterialProject

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": MATERIAL_WORKSPACE_SCHEMA,
            "operation": "model_material_edit",
            "workspace": str(self.workspace),
            "revision": self.revision,
            "subject": self.subject,
            "changes": list(self.changes),
            "history": str(self.history),
            "validation": self.project.to_dict(),
        }


def _project_from_scene(
    report: NativeAssetReport, *, source: str, edition: str,
) -> ModelMaterialProject:
    scene = report.model_scene
    findings: list[ModelMaterialFinding] = [
        ModelMaterialFinding("warning", "native_decode_warning", item)
        for item in report.warnings
    ]
    if scene is None:
        findings.append(ModelMaterialFinding(
            "error", "model_scene_unavailable",
            "The native model did not decode into renderable geometry.",
        ))
        materials: tuple[ModelMaterialRecord, ...] = ()
        geometries: tuple[ModelGeometryRecord, ...] = ()
        components: tuple[dict[str, Any], ...] = ()
        lods: tuple[str, ...] = ()
    else:
        usage: dict[int, list[int]] = {}
        geometries_list: list[ModelGeometryRecord] = []
        for index, geometry in enumerate(scene.geometries):
            if geometry.material_index is not None:
                usage.setdefault(geometry.material_index, []).append(index)
            geometries_list.append(ModelGeometryRecord(
                index=index, component=geometry.component, lod=geometry.lod,
                material_index=geometry.material_index,
                material_document_index=geometry.material_index,
                material_name=geometry.material_name,
                available_materials=tuple(item.name for item in scene.materials),
            ))
        materials = tuple(ModelMaterialRecord(
            index=item.index, shader=item.name,
            textures=tuple(MaterialTextureBinding(slot, texture, _texture_role(slot))
                           for slot, texture in item.texture_parameters),
            geometry_indices=tuple(usage.get(item.index, ())),
        ) for item in scene.materials)
        geometries = tuple(geometries_list)
        components = tuple(asdict(item) for item in scene.components)
        lods = scene.lods
        if not materials:
            findings.append(ModelMaterialFinding(
                "warning", "model_has_no_materials",
                "No shader material records were decoded from this model.",
            ))
        for material in materials:
            if not material.geometry_indices:
                findings.append(ModelMaterialFinding(
                    "info", "unused_material",
                    "Material is not referenced by decoded geometry.", material.shader,
                ))
        for geometry in geometries:
            if geometry.material_index is None:
                findings.append(ModelMaterialFinding(
                    "warning", "geometry_material_unresolved",
                    "Geometry has no resolvable shader assignment.",
                    str(geometry.index),
                ))
    return ModelMaterialProject(
        source=source, name=report.name, suffix=report.suffix,
        edition=_normalize_edition(edition), size=report.size,
        sha256=report.sha256, materials=materials, geometries=geometries,
        components=components, lods=lods, metadata=dict(report.metadata),
        findings=tuple(findings), scene=scene,
    )


def inspect_model_bytes(
    project_root: str | Path, name: str, data: bytes, *, edition: str,
    gta_path: str | Path | None = None, source: str = "",
) -> ModelMaterialProject:
    suffix = Path(name).suffix.casefold()
    if suffix not in MODEL_PREVIEW_SUFFIXES:
        raise ValueError("Model material inspection requires a YDR, YDD, or YFT asset")
    report = NativeAssetInspector(project_root, gta_path).inspect_bytes(
        Path(name).name, data, edition=_normalize_edition(edition),
    )
    return _project_from_scene(
        report, source=source or Path(name).name, edition=edition,
    )


def inspect_model_file(
    project_root: str | Path, source: str | Path, *, edition: str,
    gta_path: str | Path | None = None,
) -> ModelMaterialProject:
    authored = Path(source).expanduser()
    if authored.is_symlink():
        raise ValueError("Model inspection source cannot be a symbolic link")
    resolved = authored.resolve(strict=True)
    return inspect_model_bytes(
        project_root, resolved.name, resolved.read_bytes(), edition=edition,
        gta_path=gta_path, source=str(resolved),
    )


def inspect_model_xml(
    xml: str | Path, *, source_name: str, edition: str,
    source: str = "", revision: int | None = None,
) -> ModelMaterialProject:
    path = Path(xml).expanduser().resolve(strict=True)
    tree = _read_tree(path)
    root = tree.getroot()
    scene, metadata, warning = load_native_model_scene(path, name=source_name)
    if scene is None:
        raise ValueError(warning or "Model XML does not contain renderable geometry")
    shaders = _shader_items(root)
    shader_document_index = {id(item): index for index, item in enumerate(shaders)}
    geometry_elements = _geometry_items(root)
    usage: dict[int, list[int]] = {}
    geometries: list[ModelGeometryRecord] = []
    findings: list[ModelMaterialFinding] = []
    for index, geometry in enumerate(geometry_elements):
        raw_index = _element_value(_direct_child(geometry, "ShaderIndex"))
        local_index: int | None
        try:
            local_index = int(raw_index, 10) if raw_index else None
        except ValueError:
            local_index = None
            findings.append(ModelMaterialFinding(
                "error", "geometry_shader_index_invalid",
                "Geometry ShaderIndex is not an integer.", str(index),
            ))
        catalog = _shader_catalog_for_geometry(geometry, root)
        resolved_shader = (
            catalog[local_index]
            if local_index is not None and 0 <= local_index < len(catalog)
            else None
        )
        document_index = (
            shader_document_index.get(id(resolved_shader))
            if resolved_shader is not None else None
        )
        if document_index is not None:
            usage.setdefault(document_index, []).append(index)
        elif local_index is not None:
            findings.append(ModelMaterialFinding(
                "warning", "geometry_material_unresolved",
                "Geometry ShaderIndex does not resolve in its local shader group.",
                str(index),
            ))
        component, lod = _context_name(geometry, index)
        geometries.append(ModelGeometryRecord(
            index=index, component=component, lod=lod,
            material_index=local_index,
            material_document_index=document_index,
            material_name=(
                resolve_shader_name(
                    _element_value(_direct_child(resolved_shader, "Name"))
                )
                if resolved_shader is not None else ""
            ),
            available_materials=tuple(
                resolve_shader_name(
                    _element_value(_direct_child(item, "Name"))
                    or f"Shader {ordinal}"
                )
                for ordinal, item in enumerate(catalog)
            ),
        ))
    materials = tuple(ModelMaterialRecord(
        index=index,
        shader=resolve_shader_name(
            _element_value(_direct_child(shader, "Name")) or f"Shader {index}"
        ),
        textures=tuple(
            MaterialTextureBinding(slot, texture, _texture_role(slot))
            for slot, texture in _material_parameters(shader)
        ),
        geometry_indices=tuple(usage.get(index, ())),
    ) for index, shader in enumerate(shaders))
    if not materials:
        findings.append(ModelMaterialFinding(
            "warning", "model_has_no_materials",
            "No shader material records were found in the model XML.",
        ))
    for material in materials:
        if not material.geometry_indices:
            findings.append(ModelMaterialFinding(
                "info", "unused_material",
                "Material is not referenced by model geometry.", material.shader,
            ))
    if warning:
        findings.append(ModelMaterialFinding(
            "warning", "model_decode_warning", warning,
        ))
    size = path.stat().st_size
    return ModelMaterialProject(
        source=source or str(path), name=source_name,
        suffix=Path(source_name).suffix.casefold(), edition=_normalize_edition(edition),
        size=size, sha256=_sha256(path), materials=materials,
        geometries=tuple(geometries),
        components=tuple(asdict(item) for item in scene.components),
        lods=scene.lods, metadata=dict(metadata), findings=tuple(findings),
        revision=revision, scene=scene,
    )


class MaterialAuthoringWorkspace:
    """Revisioned shader/binding edits layered on a native XML workspace."""

    def __init__(self, workspace: str | Path) -> None:
        self._core = GuardedXmlWorkspace(
            workspace, manifest_name=MATERIAL_MANIFEST_NAME,
            operation=MATERIAL_WORKSPACE_OPERATION,
            schema_version=MATERIAL_WORKSPACE_SCHEMA,
            subject_label="Material",
        )
        self._refresh()

    @property
    def root(self) -> Path:
        return self._core.root

    @property
    def manifest(self) -> dict[str, Any]:
        return self._core.manifest

    @property
    def revision(self) -> int:
        return self._core.revision

    @property
    def xml_member(self) -> str:
        value = self.manifest.get("xml_member")
        if not isinstance(value, str) or not value:
            raise ValueError("Material workspace XML member is missing")
        return value

    @property
    def xml_path(self) -> Path:
        return self._core.member(self.xml_member)

    @classmethod
    def create(
        cls, project_root: str | Path, source: str | Path,
        destination: str | Path, *, edition: str,
        gta_path: str | Path | None = None,
    ) -> "MaterialAuthoringWorkspace":
        target = Path(destination).expanduser().resolve()
        NativeAssetInspector(project_root, gta_path).export_workspace(
            source, target, edition=_normalize_edition(edition),
        )
        try:
            return cls.initialize(target)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise

    @classmethod
    def create_bytes(
        cls, project_root: str | Path, name: str, data: bytes,
        destination: str | Path, *, edition: str,
        gta_path: str | Path | None = None, source_path: Path | None = None,
    ) -> "MaterialAuthoringWorkspace":
        target = Path(destination).expanduser().resolve()
        NativeAssetInspector(project_root, gta_path).export_workspace_bytes(
            name, data, target, edition=_normalize_edition(edition),
            source_path=source_path,
        )
        try:
            return cls.initialize(target)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise

    @classmethod
    def initialize(cls, workspace: str | Path) -> "MaterialAuthoringWorkspace":
        root = Path(workspace).expanduser().resolve()
        native_path = root / "native-workspace.json"
        material_path = root / MATERIAL_MANIFEST_NAME
        if material_path.is_file():
            return cls(root)
        if not root.is_dir() or not native_path.is_file() or native_path.is_symlink():
            raise ValueError("A verified native workspace is required")
        try:
            native = json.loads(native_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid native workspace manifest: {exc}") from exc
        if (
            not isinstance(native, dict)
            or native.get("schema_version") != NATIVE_WORKSPACE_SCHEMA
            or native.get("operation") != "native_asset_workspace"
        ):
            raise ValueError("Unsupported native workspace manifest")
        source = native.get("source")
        xml_meta = native.get("xml")
        if not isinstance(source, dict) or not isinstance(xml_meta, dict):
            raise ValueError("Native workspace has no source or XML metadata")
        source_name = source.get("name")
        xml_relative = xml_meta.get("path")
        if (
            not isinstance(source_name, str)
            or Path(source_name).name != source_name
            or Path(source_name).suffix.casefold() not in MODEL_PREVIEW_SUFFIXES
            or not isinstance(xml_relative, str)
        ):
            raise ValueError("Native workspace does not contain a supported model")
        xml_parts = PurePosixPath(xml_relative.replace("\\", "/")).parts
        if (
            len(xml_parts) != 2
            or xml_parts[0].casefold() != "edit"
            or any(part in {"", ".", ".."} for part in xml_parts)
        ):
            raise ValueError("Native model XML is outside the guarded edit directory")
        xml = (root / "edit" / xml_parts[1]).resolve()
        if not xml.is_relative_to(root) or not xml.is_file() or xml.is_symlink():
            raise ValueError("Native model XML is missing or unsafe")
        snapshot_value = source.get("snapshot")
        source_size = source.get("size")
        source_sha = source.get("sha256")
        if (
            not isinstance(snapshot_value, str)
            or not isinstance(source_size, int) or isinstance(source_size, bool)
            or not isinstance(source_sha, str) or len(source_sha) != 64
        ):
            raise ValueError("Native workspace source identity is malformed")
        snapshot_parts = PurePosixPath(snapshot_value.replace("\\", "/")).parts
        if (
            not snapshot_parts
            or any(part in {"", ".", ".."} for part in snapshot_parts)
        ):
            raise ValueError("Native workspace source snapshot path is unsafe")
        snapshot = root.joinpath(*snapshot_parts).resolve()
        if (
            not snapshot.is_relative_to(root) or not snapshot.is_file()
            or snapshot.is_symlink() or snapshot.name != source_name
            or snapshot.stat().st_size != source_size
            or _sha256(snapshot) != source_sha.casefold()
        ):
            raise ValueError("Native workspace source snapshot was modified")
        base_xml_sha = xml_meta.get("base_sha256")
        if (
            not isinstance(base_xml_sha, str) or len(base_xml_sha) != 64
            or _sha256(xml) != base_xml_sha.casefold()
        ):
            raise ValueError("Native workspace base XML was modified before initialization")
        edition = _normalize_edition(str(native.get("edition", "")))
        project = inspect_model_xml(
            xml, source_name=source_name, edition=edition,
            source=str(root), revision=0,
        )
        history = root / "history"
        if history.exists() or history.is_symlink():
            if not history.is_dir() or history.is_symlink():
                raise ValueError("Material history directory is unsafe")
        else:
            history.mkdir()
        manifest = {
            "schema_version": MATERIAL_WORKSPACE_SCHEMA,
            "operation": MATERIAL_WORKSPACE_OPERATION,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "content_root": "edit",
            "xml_member": xml.name,
            "source_name": source_name,
            "source_sha256": source.get("sha256"),
            "edition": edition,
            "revision": 0,
            "xml_sha256": project.sha256,
            "editable": [
                "existing shader names", "existing texture bindings",
                "existing geometry shader indices",
            ],
            "schema_nodes_synthesized": False,
            "game_write_performed": False,
        }
        temporary = material_path.with_name(f".{material_path.name}.tmp")
        temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        temporary.replace(material_path)
        return cls(root)

    def _refresh(self) -> None:
        self._core.refresh_manifest()
        expected = self.manifest.get("xml_sha256")
        if not isinstance(expected, str) or len(expected) != 64:
            raise ValueError("Material workspace XML hash is invalid")
        try:
            current = _sha256(self.xml_path)
        except (OSError, ValueError) as exc:
            raise ValueError("Material workspace XML is missing or unsafe") from exc
        if current != expected.casefold():
            raise ValueError(
                "Material workspace XML changed outside a recorded SDK edit"
            )

    def _check_revision(self, expected: int) -> None:
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise ValueError("Expected material revision must be a non-negative integer")
        if expected != self.revision:
            raise ValueError(
                f"Material revision conflict: expected {expected}, current revision "
                f"is {self.revision}"
            )

    def inspect(self) -> ModelMaterialProject:
        self._refresh()
        return inspect_model_xml(
            self.xml_path, source_name=str(self.manifest["source_name"]),
            edition=str(self.manifest["edition"]), source=str(self.root),
            revision=self.revision,
        )

    def set_material(
        self, material_index: int, *, expected_revision: int,
        shader_name: str | None = None,
        textures: dict[str, str] | None = None,
    ) -> MaterialAuthoringResult:
        with self._core.operation_lock():
            self._refresh()
            self._check_revision(expected_revision)
            before = self.inspect()
            tree = _read_tree(self.xml_path)
            shaders = _shader_items(tree.getroot())
            if (
                isinstance(material_index, bool) or not isinstance(material_index, int)
                or not 0 <= material_index < len(shaders)
            ):
                raise ValueError("Material index is outside the model shader catalog")
            shader = shaders[material_index]
            changes: list[dict[str, str]] = []
            if shader_name is not None:
                normalized = _validate_text(shader_name, "Shader name")
                element = _direct_child(shader, "Name")
                if element is None:
                    raise ValueError(
                        "Selected shader has no Name node; guarded authoring does not "
                        "synthesize schema fields"
                    )
                old = _element_value(element)
                if old != normalized:
                    _set_value(element, normalized)
                    changes.append({"field": "shader.name", "before": old, "after": normalized})
            updates = textures or {}
            parameters: dict[str, list[etree._Element]] = {}
            for parameter in shader.xpath(
                "./*[local-name()='Parameters']/*[local-name()='Item']"
            ):
                if parameter.get("type", "").casefold() == "texture":
                    parameters.setdefault(parameter.get("name", "").strip().casefold(), []).append(parameter)
            for requested_slot, requested_texture in updates.items():
                slot = _validate_text(requested_slot, "Texture slot")
                matches = parameters.get(slot.casefold(), [])
                if len(matches) != 1:
                    raise ValueError(
                        f"Texture slot must resolve exactly once on this material: {slot}"
                    )
                name_element = _direct_child(matches[0], "Name")
                if name_element is None:
                    raise ValueError(
                        f"Texture slot {slot} has no Name node; guarded authoring does "
                        "not synthesize schema fields"
                    )
                normalized_texture = _validate_text(
                    requested_texture, f"Texture binding {slot}", allow_empty=True,
                )
                old = _element_value(name_element)
                if old != normalized_texture:
                    _set_value(name_element, normalized_texture)
                    changes.append({
                        "field": f"texture.{slot}", "before": old,
                        "after": normalized_texture,
                    })
            if not changes:
                raise ValueError("Material edit does not change the selected shader")
            return self._commit(
                subject=f"material:{material_index}", tree=tree,
                changes=tuple(changes), before=before,
            )

    def set_geometry_material(
        self, geometry_index: int, material_index: int, *, expected_revision: int,
    ) -> MaterialAuthoringResult:
        with self._core.operation_lock():
            self._refresh()
            self._check_revision(expected_revision)
            before = self.inspect()
            tree = _read_tree(self.xml_path)
            root = tree.getroot()
            geometries = _geometry_items(root)
            if (
                isinstance(geometry_index, bool) or not isinstance(geometry_index, int)
                or not 0 <= geometry_index < len(geometries)
            ):
                raise ValueError("Geometry index is outside the model geometry catalog")
            geometry = geometries[geometry_index]
            catalog = _shader_catalog_for_geometry(geometry, root)
            if (
                isinstance(material_index, bool) or not isinstance(material_index, int)
                or not 0 <= material_index < len(catalog)
            ):
                raise ValueError("Material index is outside this geometry's local shader group")
            element = _direct_child(geometry, "ShaderIndex")
            if element is None:
                raise ValueError(
                    "Selected geometry has no ShaderIndex node; guarded authoring does "
                    "not synthesize schema fields"
                )
            old = _element_value(element)
            new = str(material_index)
            if old == new:
                raise ValueError("Geometry already uses the selected material")
            _set_value(element, new)
            return self._commit(
                subject=f"geometry:{geometry_index}", tree=tree,
                changes=({"field": "geometry.shaderIndex", "before": old, "after": new},),
                before=before,
            )

    def _commit(
        self, *, subject: str, tree: etree._ElementTree,
        changes: tuple[dict[str, str], ...], before: ModelMaterialProject,
    ) -> MaterialAuthoringResult:
        history = self._core.snapshot(
            subject, (self.xml_member,), changes,
            operation="model_material_xml_edit",
        )
        previous_manifest = dict(self.manifest)
        temporary = self.xml_path.with_name(f".{self.xml_path.name}.material.tmp")
        try:
            tree.write(
                str(temporary), encoding="utf-8", xml_declaration=True,
                pretty_print=False,
            )
            after = inspect_model_xml(
                temporary, source_name=str(self.manifest["source_name"]),
                edition=str(self.manifest["edition"]), source=str(self.root),
                revision=self.revision + 1,
            )
            if len(after.materials) != len(before.materials):
                raise RuntimeError("Material edit changed the shader catalog shape")
            if len(after.geometries) != len(before.geometries):
                raise RuntimeError("Material edit changed the model geometry shape")
            temporary.replace(self.xml_path)
            self._core.record_post_edit_state(history)
            revision = self.revision + 1
            self.manifest["revision"] = revision
            self.manifest["updated_utc"] = datetime.now(timezone.utc).isoformat()
            self.manifest["xml_sha256"] = _sha256(self.xml_path)
            (history / "validation.json").write_text(
                json.dumps(after.to_dict(), indent=2) + "\n", encoding="utf-8",
            )
            self._core.write_manifest()
        except Exception:
            temporary.unlink(missing_ok=True)
            self.manifest.clear()
            self.manifest.update(previous_manifest)
            self._core.restore(history)
            shutil.rmtree(history, ignore_errors=True)
            raise
        return MaterialAuthoringResult(
            self.root, revision, subject, changes, history, after,
        )

    def undo(self, *, expected_revision: int) -> MaterialAuthoringResult:
        with self._core.operation_lock():
            self._refresh()
            self._check_revision(expected_revision)
            history = self._core.latest_history()
            self._core.verify_post_edit_state(history)
            record = self._core.history_record(history)
            subject = str(record.get("subject", ""))
            changes = tuple(
                dict(item) for item in record.get("changes", ())
                if isinstance(item, dict)
            )
            recovery = self._core.snapshot_current_for_undo(history)
            previous_manifest = dict(self.manifest)
            undone = history.with_name(f"{history.name}.undone")
            try:
                self._core.restore(history)
                revision = self.revision + 1
                project = inspect_model_xml(
                    self.xml_path, source_name=str(self.manifest["source_name"]),
                    edition=str(self.manifest["edition"]), source=str(self.root),
                    revision=revision,
                )
                self.manifest["revision"] = revision
                self.manifest["updated_utc"] = datetime.now(timezone.utc).isoformat()
                self.manifest["xml_sha256"] = _sha256(self.xml_path)
                history.rename(undone)
                self._core.write_manifest()
            except Exception:
                self.manifest.clear()
                self.manifest.update(previous_manifest)
                if undone.exists() and not history.exists():
                    undone.rename(history)
                self._core.restore(recovery)
                shutil.rmtree(recovery, ignore_errors=True)
                raise
            shutil.rmtree(recovery, ignore_errors=True)
            return MaterialAuthoringResult(
                self.root, revision, subject, changes, undone, project,
            )

    def build(
        self, project_root: str | Path, output: str | Path,
        *, gta_path: str | Path | None = None,
    ) -> tuple[Path, Path]:
        self._refresh()
        return NativeAssetInspector(project_root, gta_path).build_workspace(
            self.root, output,
        )


__all__ = [
    "MATERIAL_MANIFEST_NAME",
    "MATERIAL_PROJECT_SCHEMA",
    "MATERIAL_WORKSPACE_SCHEMA",
    "MaterialAuthoringResult",
    "MaterialAuthoringWorkspace",
    "MaterialTextureBinding",
    "ModelGeometryRecord",
    "ModelMaterialFinding",
    "ModelMaterialProject",
    "ModelMaterialRecord",
    "inspect_model_bytes",
    "inspect_model_file",
    "inspect_model_xml",
]
