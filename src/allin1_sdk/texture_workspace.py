"""Guarded texture-dictionary editing inside native YTD workspaces."""

from __future__ import annotations

import hashlib
import json
import shutil
import struct
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image, UnidentifiedImageError


MAX_TEXTURE_XML_BYTES = 256 * 1024 * 1024
MAX_TEXTURE_SOURCE_BYTES = 512 * 1024 * 1024
MAX_TEXTURE_DIMENSION = 16_384
MAX_TEXTURE_PIXELS = 128 * 1024 * 1024
MAX_YTD_TEXTURES = 4_096
RASTER_TEXTURE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp"})


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


@dataclass(frozen=True)
class DdsMetadata:
    width: int
    height: int
    mip_levels: int
    format: str


@dataclass(frozen=True)
class TextureRecord:
    name: str
    file_name: str
    width: int
    height: int
    mip_levels: int
    format: str
    usage: str
    size: int | None
    sha256: str | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TextureCatalog:
    workspace: Path
    xml: Path
    assets: Path
    textures: tuple[TextureRecord, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "operation": "ytd_texture_catalog",
            "workspace": str(self.workspace),
            "xml": str(self.xml),
            "assets": str(self.assets),
            "texture_count": len(self.textures),
            "warnings": list(self.warnings),
            "textures": [asdict(item) for item in self.textures],
        }


@dataclass(frozen=True)
class TextureEditResult:
    action: str
    texture: TextureRecord
    history: Path
    catalog: TextureCatalog


@dataclass(frozen=True)
class TextureRestoreResult:
    restored: Path
    recovery_history: Path
    catalog: TextureCatalog


def inspect_dds(path: str | Path) -> DdsMetadata:
    """Read and validate the DDS header fields used by GTA texture dictionaries."""
    source = Path(path)
    with source.open("rb") as stream:
        header = stream.read(148)
    if len(header) < 128 or header[:4] != b"DDS ":
        raise ValueError(f"Texture is not a valid DDS file: {source}")
    if struct.unpack_from("<I", header, 4)[0] != 124:
        raise ValueError("DDS header has an invalid structure size")
    height, width = struct.unpack_from("<II", header, 12)
    mip_levels = struct.unpack_from("<I", header, 28)[0] or 1
    if not (0 < width <= MAX_TEXTURE_DIMENSION and 0 < height <= MAX_TEXTURE_DIMENSION):
        raise ValueError("DDS dimensions are empty or exceed the guarded limit")
    if width * height > MAX_TEXTURE_PIXELS or not (1 <= mip_levels <= 16):
        raise ValueError("DDS pixel or mip count exceeds the guarded limit")
    if struct.unpack_from("<I", header, 76)[0] != 32:
        raise ValueError("DDS pixel-format header is invalid")
    flags = struct.unpack_from("<I", header, 80)[0]
    fourcc = header[84:88]
    format_name: str | None = None
    fourcc_formats = {
        b"DXT1": "D3DFMT_DXT1", b"DXT2": "D3DFMT_DXT3",
        b"DXT3": "D3DFMT_DXT3", b"DXT4": "D3DFMT_DXT5",
        b"DXT5": "D3DFMT_DXT5", b"ATI1": "D3DFMT_ATI1",
        b"ATI2": "D3DFMT_ATI2", b"BC4U": "D3DFMT_ATI1",
        b"BC5U": "D3DFMT_ATI2", b"BC7 ": "D3DFMT_BC7",
    }
    if fourcc == b"DX10":
        if len(header) < 148:
            raise ValueError("DDS DX10 header is truncated")
        dxgi = struct.unpack_from("<I", header, 128)[0]
        format_name = {
            28: "D3DFMT_A8B8G8R8", 65: "D3DFMT_A8", 61: "D3DFMT_L8",
            71: "D3DFMT_DXT1", 72: "D3DFMT_DXT1",
            74: "D3DFMT_DXT3", 75: "D3DFMT_DXT3",
            77: "D3DFMT_DXT5", 78: "D3DFMT_DXT5",
            80: "D3DFMT_ATI1", 83: "D3DFMT_ATI2", 87: "D3DFMT_A8R8G8B8",
            88: "D3DFMT_X8R8G8B8", 98: "D3DFMT_BC7", 99: "D3DFMT_BC7",
        }.get(dxgi)
    elif flags & 0x4:
        format_name = fourcc_formats.get(fourcc)
        if format_name is None:
            numeric = struct.unpack("<I", fourcc)[0]
            format_name = {
                21: "D3DFMT_A8R8G8B8", 22: "D3DFMT_X8R8G8B8",
                25: "D3DFMT_A1R5G5B5", 28: "D3DFMT_A8",
                32: "D3DFMT_A8B8G8R8", 50: "D3DFMT_L8",
            }.get(numeric)
    elif flags & 0x40:
        bits, red, green, blue, alpha = struct.unpack_from("<IIIII", header, 88)
        format_name = {
            (32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000):
                "D3DFMT_A8R8G8B8",
            (32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0):
                "D3DFMT_X8R8G8B8",
            (32, 0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000):
                "D3DFMT_A8B8G8R8",
            (16, 0x00007C00, 0x000003E0, 0x0000001F, 0x00008000):
                "D3DFMT_A1R5G5B5",
        }.get((bits, red, green, blue, alpha))
    elif flags & 0x2:
        format_name = "D3DFMT_A8"
    elif flags & 0x20000:
        bits = struct.unpack_from("<I", header, 88)[0]
        format_name = "D3DFMT_L8" if bits == 8 else None
    if format_name is None:
        raise ValueError(
            f"DDS uses a texture format that GTA V/CodeWalker import cannot identify: "
            f"{fourcc!r}"
        )
    return DdsMetadata(width, height, mip_levels, format_name)


class TextureDictionaryWorkspace:
    """List and mutate textures while retaining a local undo history."""

    def __init__(self, workspace: str | Path) -> None:
        authored = Path(workspace).expanduser()
        if authored.is_symlink():
            raise ValueError("YTD workspace cannot be a symbolic link")
        self.root = authored.resolve()
        manifest_path = self.root / "native-workspace.json"
        if not self.root.is_dir() or not manifest_path.is_file() or manifest_path.is_symlink():
            raise ValueError("YTD workspace or its native manifest is missing or unsafe")
        try:
            self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid native workspace manifest: {exc}") from exc
        source = self.manifest.get("source") if isinstance(self.manifest, dict) else None
        xml_meta = self.manifest.get("xml") if isinstance(self.manifest, dict) else None
        if not isinstance(source, dict) or str(source.get("suffix", "")).casefold() != ".ytd":
            raise ValueError("Texture editing requires a native .ytd workspace")
        if not isinstance(xml_meta, dict):
            raise ValueError("YTD workspace has no XML metadata")
        self.xml = self._member(xml_meta.get("path"), "XML")
        self.assets = (self.root / "edit" / "assets").resolve()
        if not self.xml.is_file() or self.xml.is_symlink():
            raise ValueError("YTD workspace XML is missing or unsafe")
        if not self.assets.is_dir() or self.assets.is_symlink():
            raise ValueError("YTD workspace asset folder is missing or unsafe")

    def catalog(self) -> TextureCatalog:
        tree, items = self._tree()
        del tree
        names: set[str] = set()
        files: set[str] = set()
        records: list[TextureRecord] = []
        catalog_warnings: list[str] = []
        for item in items:
            name = self._text(item, "Name")
            file_name = self._text(item, "FileName")
            if not name or not file_name:
                raise ValueError("Every YTD texture requires Name and FileName")
            name_key = name.casefold()
            file_key = file_name.casefold()
            if name_key in names or file_key in files:
                raise ValueError(f"YTD contains a duplicate texture name or file: {name}")
            names.add(name_key)
            files.add(file_key)
            dependency = self._asset_member(file_name)
            width = self._number(item, "Width")
            height = self._number(item, "Height")
            mip_levels = self._number(item, "MipLevels")
            if (
                not 0 < width <= MAX_TEXTURE_DIMENSION
                or not 0 < height <= MAX_TEXTURE_DIMENSION
                or width * height > MAX_TEXTURE_PIXELS
                or not 1 <= mip_levels <= 16
            ):
                raise ValueError(f"YTD texture dimensions or mip count are unsafe: {name}")
            format_name = self._text(item, "Format")
            warnings: list[str] = []
            size: int | None = None
            sha256: str | None = None
            if not dependency.is_file() or dependency.is_symlink():
                warnings.append("DDS dependency is missing or unsafe")
            else:
                size = dependency.stat().st_size
                sha256 = _sha256_file(dependency)
                try:
                    actual = inspect_dds(dependency)
                except ValueError as exc:
                    warnings.append(str(exc))
                else:
                    if (actual.width, actual.height) != (width, height):
                        warnings.append(
                            f"XML dimensions {width}x{height} differ from DDS "
                            f"{actual.width}x{actual.height}"
                        )
                    if actual.mip_levels != mip_levels:
                        warnings.append(
                            f"XML mip count {mip_levels} differs from DDS {actual.mip_levels}"
                        )
                    if actual.format.casefold() != format_name.casefold():
                        warnings.append(
                            f"XML format {format_name} differs from DDS {actual.format}"
                        )
            if warnings:
                catalog_warnings.append(f"{name}: {'; '.join(warnings)}")
            records.append(TextureRecord(
                name=name, file_name=file_name, width=width, height=height,
                mip_levels=mip_levels, format=format_name,
                usage=self._text(item, "Usage"), size=size, sha256=sha256,
                warnings=tuple(warnings),
            ))
        return TextureCatalog(
            self.root, self.xml, self.assets, tuple(records), tuple(catalog_warnings),
        )

    def replace(self, texture_name: str, source_image: str | Path) -> TextureEditResult:
        tree, items = self._tree()
        item = self._find(items, texture_name)
        file_name = self._text(item, "FileName")
        destination = self._asset_member(file_name)
        history = self._history("replace", texture_name, destination)
        with tempfile.TemporaryDirectory(
            prefix=".allin1-texture-", dir=self.assets,
        ) as temporary:
            staged = Path(temporary) / "replacement.dds"
            metadata = self._prepare_dds(source_image, staged)
            self._update_item(item, metadata)
            self._commit_tree_and_dependency(tree, staged, destination, history)
        catalog = self.catalog()
        texture = next(item for item in catalog.textures if item.name.casefold() == texture_name.casefold())
        return TextureEditResult("replace", texture, history, catalog)

    def add(self, texture_name: str, source_image: str | Path) -> TextureEditResult:
        normalized = self._safe_texture_name(texture_name)
        tree, items = self._tree()
        if any(self._text(item, "Name").casefold() == normalized.casefold() for item in items):
            raise ValueError(f"YTD texture already exists: {normalized}")
        file_name = f"{normalized}.dds"
        destination = self._asset_member(file_name)
        if destination.exists() or destination.is_symlink():
            raise ValueError(f"YTD dependency already exists: {file_name}")
        history = self._history("add", normalized, destination)
        with tempfile.TemporaryDirectory(
            prefix=".allin1-texture-", dir=self.assets,
        ) as temporary:
            staged = Path(temporary) / "addition.dds"
            metadata = self._prepare_dds(source_image, staged)
            item = ET.SubElement(tree.getroot(), "Item")
            ET.SubElement(item, "Name").text = normalized
            ET.SubElement(item, "Unk32", {"value": "0"})
            ET.SubElement(item, "Usage").text = "DEFAULT"
            ET.SubElement(item, "UsageFlags").text = "0"
            ET.SubElement(item, "ExtraFlags", {"value": "0"})
            ET.SubElement(item, "Width", {"value": str(metadata.width)})
            ET.SubElement(item, "Height", {"value": str(metadata.height)})
            ET.SubElement(item, "MipLevels", {"value": str(metadata.mip_levels)})
            ET.SubElement(item, "Format").text = metadata.format
            ET.SubElement(item, "FileName").text = file_name
            self._commit_tree_and_dependency(tree, staged, destination, history)
        catalog = self.catalog()
        texture = next(item for item in catalog.textures if item.name.casefold() == normalized.casefold())
        return TextureEditResult("add", texture, history, catalog)

    def remove(self, texture_name: str) -> TextureEditResult:
        tree, items = self._tree()
        item = self._find(items, texture_name)
        old_record = next(
            record for record in self.catalog().textures
            if record.name.casefold() == texture_name.casefold()
        )
        dependency = self._asset_member(self._text(item, "FileName"))
        history = self._history("remove", texture_name, dependency)
        tree.getroot().remove(item)
        previous_xml = (history / "workspace.xml").read_bytes()
        try:
            self._write_tree(tree)
            if dependency.is_file():
                dependency.unlink()
        except Exception:
            self.xml.write_bytes(previous_xml)
            backup = history / "dependency.dds"
            if backup.is_file() and not dependency.exists():
                shutil.copyfile(backup, dependency)
            raise
        return TextureEditResult("remove", old_record, history, self.catalog())

    def restore_latest(self) -> TextureRestoreResult:
        """Restore the most recent edit snapshot and retain the pre-restore state."""
        history_root = self.root / "history"
        if not history_root.is_dir() or history_root.is_symlink():
            raise ValueError("YTD workspace has no safe edit history")
        candidates = sorted(
            (
                path for path in history_root.iterdir()
                if path.is_dir() and not path.is_symlink()
                and (path / "edit.json").is_file()
            ),
            key=lambda path: path.name,
            reverse=True,
        )
        candidates = [
            path for path in candidates
            if "-restore-backup-" not in path.name and not path.name.endswith(".restored")
        ]
        if not candidates:
            raise ValueError("YTD workspace has no restorable edit history")
        selected = candidates[0]
        try:
            record = json.loads((selected / "edit.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid YTD edit history: {exc}") from exc
        if not isinstance(record, dict) or record.get("operation") != "ytd_texture_edit":
            raise ValueError("Unsupported YTD edit history record")
        texture_name = str(record.get("texture", ""))
        dependency_value = record.get("dependency")
        dependency = (
            self._asset_member(dependency_value)
            if isinstance(dependency_value, str) and dependency_value else None
        )
        snapshot_xml = selected / "workspace.xml"
        snapshot_dependency = selected / "dependency.dds"
        if not snapshot_xml.is_file() or snapshot_xml.is_symlink():
            raise ValueError("YTD history XML snapshot is missing or unsafe")
        recovery = self._history("restore-backup", texture_name, dependency)
        current_xml = (recovery / "workspace.xml").read_bytes()
        current_dependency = recovery / "dependency.dds"
        try:
            self.xml.write_bytes(snapshot_xml.read_bytes())
            if dependency is not None:
                dependency.parent.mkdir(parents=True, exist_ok=True)
                if snapshot_dependency.is_file() and not snapshot_dependency.is_symlink():
                    shutil.copyfile(snapshot_dependency, dependency)
                elif dependency.exists():
                    dependency.unlink()
            catalog = self.catalog()
        except Exception:
            self.xml.write_bytes(current_xml)
            if dependency is not None:
                if current_dependency.is_file():
                    shutil.copyfile(current_dependency, dependency)
                elif dependency.is_file():
                    dependency.unlink()
            raise
        # Prevent the same edit from being selected repeatedly while retaining it
        # as an auditable snapshot.
        restored = selected.with_name(f"{selected.name}.restored")
        selected.rename(restored)
        return TextureRestoreResult(restored, recovery, catalog)

    def _tree(self) -> tuple[ET.ElementTree, list[ET.Element]]:
        size = self.xml.stat().st_size
        if not 0 < size <= MAX_TEXTURE_XML_BYTES:
            raise ValueError("YTD XML is empty or exceeds the guarded limit")
        try:
            tree = ET.parse(self.xml)
        except (ET.ParseError, OSError) as exc:
            raise ValueError(f"Invalid YTD XML: {exc}") from exc
        root = tree.getroot()
        if root.tag != "TextureDictionary":
            raise ValueError("YTD XML root must be TextureDictionary")
        items = list(root.findall("Item"))
        if len(items) > MAX_YTD_TEXTURES:
            raise ValueError("YTD texture count exceeds the guarded limit")
        return tree, items

    def _history(
        self, action: str, texture_name: str, dependency: Path | None,
    ) -> Path:
        history_root = self.root / "history"
        if history_root.is_symlink():
            raise ValueError("YTD workspace history folder is unsafe")
        history_root.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        target = history_root / f"{stamp}-{action}-{self._safe_texture_name(texture_name)}"
        target.mkdir()
        shutil.copyfile(self.xml, target / "workspace.xml")
        if dependency is not None and dependency.is_file() and not dependency.is_symlink():
            shutil.copyfile(dependency, target / "dependency.dds")
        _write_json_atomic(target / "edit.json", {
            "schema_version": 1, "operation": "ytd_texture_edit",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "action": action, "texture": texture_name,
            "dependency": (
                dependency.relative_to(self.assets).as_posix()
                if dependency is not None else None
            ),
            "xml_sha256": _sha256_file(target / "workspace.xml"),
            "dependency_sha256": (
                _sha256_file(target / "dependency.dds")
                if (target / "dependency.dds").is_file() else None
            ),
        })
        return target

    def _prepare_dds(self, source_image: str | Path, destination: Path) -> DdsMetadata:
        authored = Path(source_image).expanduser()
        if authored.is_symlink():
            raise ValueError("Texture source cannot be a symbolic link")
        source = authored.resolve()
        if not source.is_file() or not 0 < source.stat().st_size <= MAX_TEXTURE_SOURCE_BYTES:
            raise ValueError("Texture source is missing, empty, or exceeds the guarded limit")
        suffix = source.suffix.casefold()
        if suffix == ".dds":
            metadata = inspect_dds(source)
            shutil.copyfile(source, destination)
            return metadata
        if suffix not in RASTER_TEXTURE_SUFFIXES:
            raise ValueError("Texture source must be DDS, PNG, JPEG, BMP, TGA, or WebP")
        try:
            with Image.open(source) as opened:
                if (
                    opened.width <= 0 or opened.height <= 0
                    or opened.width > MAX_TEXTURE_DIMENSION
                    or opened.height > MAX_TEXTURE_DIMENSION
                    or opened.width * opened.height > MAX_TEXTURE_PIXELS
                ):
                    raise ValueError("Raster texture dimensions exceed the guarded limit")
                opened.load()
                opened.convert("RGBA").save(destination, format="DDS")
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
            raise ValueError(f"Could not decode texture image: {exc}") from exc
        return inspect_dds(destination)

    def _commit_tree_and_dependency(
        self, tree: ET.ElementTree, staged: Path, destination: Path, history: Path,
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        staged_dependency = destination.with_name(f".{destination.name}.allin1-new")
        shutil.copyfile(staged, staged_dependency)
        previous_xml = (history / "workspace.xml").read_bytes()
        backup = history / "dependency.dds"
        try:
            staged_dependency.replace(destination)
            self._write_tree(tree)
        except Exception:
            self.xml.write_bytes(previous_xml)
            if backup.is_file():
                shutil.copyfile(backup, destination)
            elif destination.is_file():
                destination.unlink()
            try:
                staged_dependency.unlink()
            except FileNotFoundError:
                pass
            raise

    def _write_tree(self, tree: ET.ElementTree) -> None:
        ET.indent(tree, space=" ")
        temporary = self.xml.with_name(f".{self.xml.name}.allin1-new")
        tree.write(temporary, encoding="utf-8", xml_declaration=True)
        temporary.replace(self.xml)

    def _member(self, value: object, label: str) -> Path:
        if not isinstance(value, str) or not value:
            raise ValueError(f"YTD workspace {label} path is missing")
        relative = PurePosixPath(value.replace("\\", "/"))
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError(f"YTD workspace {label} path is unsafe")
        resolved = self.root.joinpath(*relative.parts).resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError(f"YTD workspace {label} path escapes its root")
        return resolved

    def _asset_member(self, value: str) -> Path:
        relative = PurePosixPath(value.replace("\\", "/"))
        if (
            relative.is_absolute() or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError(f"YTD dependency path is unsafe: {value}")
        resolved = self.assets.joinpath(*relative.parts).resolve()
        if not resolved.is_relative_to(self.assets):
            raise ValueError(f"YTD dependency path escapes its asset folder: {value}")
        return resolved

    @staticmethod
    def _text(item: ET.Element, name: str) -> str:
        node = item.find(name)
        if node is None:
            return ""
        return (node.text or node.get("value") or "").strip()

    @classmethod
    def _number(cls, item: ET.Element, name: str) -> int:
        value = cls._text(item, name)
        try:
            number = int(value)
        except ValueError as exc:
            raise ValueError(f"YTD texture has an invalid {name}: {value}") from exc
        if number < 0:
            raise ValueError(f"YTD texture has a negative {name}")
        return number

    @classmethod
    def _find(cls, items: list[ET.Element], texture_name: str) -> ET.Element:
        matches = [
            item for item in items
            if cls._text(item, "Name").casefold() == texture_name.strip().casefold()
        ]
        if len(matches) != 1:
            raise ValueError(f"YTD texture was not found uniquely: {texture_name}")
        return matches[0]

    @staticmethod
    def _update_item(item: ET.Element, metadata: DdsMetadata) -> None:
        for name, value in (
            ("Width", metadata.width), ("Height", metadata.height),
            ("MipLevels", metadata.mip_levels),
        ):
            node = item.find(name)
            if node is None:
                node = ET.SubElement(item, name)
            node.attrib.clear()
            node.set("value", str(value))
            node.text = None
        format_node = item.find("Format")
        if format_node is None:
            format_node = ET.SubElement(item, "Format")
        format_node.text = metadata.format

    @staticmethod
    def _safe_texture_name(value: str) -> str:
        name = value.strip()
        if (
            not name or len(name) > 120 or name in {".", ".."}
            or any(character in name for character in '<>:"/\\|?*\0')
        ):
            raise ValueError("Texture name is empty or contains unsafe filename characters")
        return name
