"""Inspection and guarded XML round-tripping of native GTA V assets.

The lightweight parsers in this module always work and intentionally stop at
well-defined headers.  When the pinned RpfPatcher/CodeWalker helper is present,
supported RAGE resources are additionally converted to their structured XML
representation and texture dictionaries receive a visual contact sheet. Supported
resources can also be exported into snapshot-backed editing workspaces and rebuilt
only after the result successfully reparses through CodeWalker.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import struct
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image, ImageDraw, ImageOps, UnidentifiedImageError

from allin1_sdk.gxt2_workspace import Gxt2Workspace
from allin1_sdk.processes import run_hidden


NATIVE_XML_SUFFIXES = frozenset({
    ".awc", ".gxt2", ".rel", ".ybn", ".ycd", ".ydd", ".ydr",
    ".yed", ".yfd", ".yft", ".ymap", ".ymf", ".ymt", ".ynd",
    ".ynv", ".ypt", ".ytd", ".ytyp", ".yvr", ".ywr",
})
NATIVE_XML_IMPORT_SUFFIXES = frozenset({
    ".awc", ".rel", ".ybn", ".ycd", ".ydd", ".ydr", ".yed",
    ".yfd", ".yft", ".ymap", ".ymf", ".ymt", ".ynd", ".ynv",
    ".ypt", ".ytd", ".ytyp", ".yvr", ".ywr",
})
NATIVE_ASSET_SUFFIXES = NATIVE_XML_SUFFIXES | frozenset({
    ".awc", ".gfx", ".rel", ".rpf", ".ycd", ".yed", ".yfd",
    ".ymf", ".ynd", ".ynv", ".ypt", ".yvr", ".ywr",
})
MAX_NATIVE_PREVIEW_BYTES = 128 * 1024 * 1024
MAX_NATIVE_WORKSPACE_FILES = 10_000
MAX_NATIVE_WORKSPACE_BYTES = 2 * 1024 * 1024 * 1024
NATIVE_WORKSPACE_SCHEMA = 1


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
class NativeAssetReport:
    name: str
    suffix: str
    format_name: str
    size: int
    sha256: str
    metadata: dict[str, Any] = field(default_factory=dict)
    structured_text: str | None = None
    image_png: bytes | None = None
    warnings: tuple[str, ...] = ()

    def summary(self) -> str:
        lines = [
            f"Format: {self.format_name}",
            f"Size: {self.size:,} bytes",
            f"SHA-256: {self.sha256}",
        ]
        lines.extend(f"{key}: {value}" for key, value in self.metadata.items())
        if self.warnings:
            lines.extend(("", "Warnings:"))
            lines.extend(f"• {warning}" for warning in self.warnings)
        return "\n".join(lines)


def _rsc_metadata(data: bytes) -> dict[str, Any]:
    if len(data) < 16 or data[:4] not in {b"RSC7", b"RSC8"}:
        return {}
    return {
        "resource_container": data[:4].decode("ascii"),
        "resource_version": int.from_bytes(data[4:8], "little"),
        "system_flags": f"0x{int.from_bytes(data[8:12], 'little'):08X}",
        "graphics_flags": f"0x{int.from_bytes(data[12:16], 'little'):08X}",
    }


def _dds_metadata(data: bytes) -> dict[str, Any]:
    if len(data) < 128 or not data.startswith(b"DDS "):
        return {}
    height, width = struct.unpack_from("<II", data, 12)
    mipmaps = struct.unpack_from("<I", data, 28)[0]
    fourcc = data[84:88].rstrip(b"\0").decode("ascii", errors="replace")
    return {
        "dimensions": f"{width} × {height}",
        "mip_levels": mipmaps or 1,
        "pixel_format": fourcc or "uncompressed",
    }


def _gxt2_text(data: bytes) -> tuple[str | None, dict[str, Any], tuple[str, ...]]:
    if len(data) < 16 or data[:4] != b"2TXG":
        return None, {}, ()
    count = int.from_bytes(data[4:8], "little")
    try:
        entries = Gxt2Workspace.parse(data)
    except ValueError as exc:
        return None, {"label_count": count}, (str(exc),)
    lines = [f"{item['hash_hex']}  {item['text']}" for item in entries]
    return "\n".join(lines), {"label_count": len(entries)}, ()


def _format_identity(name: str, data: bytes) -> tuple[str, dict[str, Any]]:
    suffix = Path(name).suffix.casefold()
    metadata = _rsc_metadata(data)
    if data.startswith(b"RPF7"):
        return "Rockstar RPF7 archive", metadata
    if data.startswith(b"DDS "):
        metadata.update(_dds_metadata(data))
        return "DirectDraw Surface texture", metadata
    if data.startswith(b"2TXG"):
        return "Rockstar GXT2 text table", metadata
    if data[:4] in {b"ADAT", b"TADA"}:
        metadata["endianness"] = "little" if data[:4] == b"ADAT" else "big"
        if len(data) >= 12:
            metadata["awc_version"] = int.from_bytes(data[4:6], "little")
            metadata["stream_count"] = int.from_bytes(data[8:12], "little")
        return "Rockstar AWC audio container", metadata
    if data[:3] in {b"FWS", b"CWS", b"ZWS"}:
        metadata["scaleform_signature"] = data[:3].decode("ascii")
        metadata["scaleform_version"] = data[3] if len(data) > 3 else "unknown"
        return "Scaleform/SWF interface movie", metadata
    names = {
        ".rpf": "Rockstar RPF archive", ".ytd": "Rockstar texture dictionary",
        ".ydr": "Rockstar drawable", ".ydd": "Rockstar drawable dictionary",
        ".yft": "Rockstar fragment", ".ybn": "Rockstar collision bounds",
        ".ymap": "Rockstar map placement", ".ytyp": "Rockstar archetype definition",
        ".ymt": "Rockstar metadata resource", ".gxt2": "Rockstar GXT2 text table",
        ".awc": "Rockstar AWC audio container", ".rel": "Rockstar audio relationship",
        ".gfx": "Scaleform interface movie", ".ycd": "Rockstar clip dictionary",
        ".ynd": "Rockstar path nodes", ".ynv": "Rockstar navigation mesh",
        ".ypt": "Rockstar particle dictionary",
    }
    return names.get(suffix, "Binary asset"), metadata


def _texture_contact_sheet(folder: Path) -> tuple[bytes | None, int]:
    candidates = sorted(
        (path for path in folder.rglob("*") if path.suffix.casefold() in {
            ".dds", ".png", ".jpg", ".jpeg", ".bmp",
        }),
        key=lambda path: path.name.casefold(),
    )
    thumbnails: list[tuple[Image.Image, str]] = []
    for path in candidates[:24]:
        try:
            with Image.open(path) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGBA")
                image.thumbnail((190, 150), Image.Resampling.LANCZOS)
                thumbnails.append((image.copy(), path.stem))
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError):
            continue
    if not thumbnails:
        return None, len(candidates)
    columns = 4
    cell_width, cell_height = 210, 185
    rows = (len(thumbnails) + columns - 1) // columns
    sheet = Image.new("RGBA", (columns * cell_width, rows * cell_height), "#18201d")
    draw = ImageDraw.Draw(sheet)
    for index, (image, label) in enumerate(thumbnails):
        left = (index % columns) * cell_width
        top = (index // columns) * cell_height
        x = left + ((cell_width - image.width) // 2)
        y = top + 5 + ((150 - image.height) // 2)
        sheet.alpha_composite(image, (x, y))
        draw.text((left + 8, top + 160), label[:30], fill="#E8F2EC")
    output = io.BytesIO()
    sheet.convert("RGB").save(output, format="PNG")
    return output.getvalue(), len(candidates)


class NativeAssetInspector:
    """Describe native files and optionally invoke CodeWalker XML conversion."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.patcher = self.project_root / "tools" / "RpfPatcher" / "RpfPatcher.exe"

    def inspect_bytes(
        self, name: str, data: bytes, *, edition: str = "Enhanced",
        truncated: bool = False,
    ) -> NativeAssetReport:
        suffix = Path(name).suffix.casefold()
        format_name, metadata = _format_identity(name, data)
        warnings: list[str] = []
        structured: str | None = None
        image_png: bytes | None = None
        if truncated:
            warnings.append("Deep preview skipped because the asset exceeded the safety limit.")
        if suffix == ".gxt2" and not truncated:
            structured, gxt_metadata, gxt_warnings = _gxt2_text(data)
            metadata.update(gxt_metadata)
            warnings.extend(gxt_warnings)
        if (not truncated and suffix in NATIVE_XML_SUFFIXES
                and self.patcher.is_file()):
            converted = self._convert(name, data, edition)
            if converted is not None and converted[3]:
                alternate = "Legacy" if edition.casefold() == "enhanced" else "Enhanced"
                retried = self._convert(name, data, alternate)
                if retried is not None and not retried[3]:
                    converted = retried
                    metadata["interpreted_edition"] = alternate
                    warnings.append(
                        f"Requested {edition} decoding failed; the resource parsed as {alternate}."
                    )
            if converted is not None:
                structured, image_png, texture_count, conversion_warning = converted
                if texture_count:
                    metadata["exported_textures"] = texture_count
                if conversion_warning:
                    warnings.append(conversion_warning)
        elif suffix in NATIVE_XML_SUFFIXES and not self.patcher.is_file():
            warnings.append(
                "RpfPatcher is not built; run runtools.ps1 for structured CodeWalker preview."
            )
        return NativeAssetReport(
            name=name, suffix=suffix, format_name=format_name, size=len(data),
            sha256=hashlib.sha256(data).hexdigest(), metadata=metadata,
            structured_text=structured, image_png=image_png,
            warnings=tuple(warnings),
        )

    def _convert(
        self, name: str, data: bytes, edition: str,
    ) -> tuple[str, bytes | None, int, str | None] | None:
        with tempfile.TemporaryDirectory(prefix="allin1-native-asset-") as temporary:
            root = Path(temporary)
            safe_name = Path(name).name or f"asset{Path(name).suffix}"
            source = root / safe_name
            xml = root / f"{safe_name}.xml"
            assets = root / "assets"
            source.write_bytes(data)
            completed = run_hidden(
                [
                    self.patcher, "asset-xml", source, xml, assets,
                    "gen9" if edition.casefold() == "enhanced" else "legacy",
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if completed.returncode or not xml.is_file():
                detail = (completed.stderr or completed.stdout or "conversion failed").strip()
                return None if not detail else (
                    "", None, 0, f"CodeWalker preview failed: {detail}"
                )
            xml_size = xml.stat().st_size
            with xml.open("r", encoding="utf-8", errors="replace") as stream:
                text = stream.read(2_000_000)
            if xml_size > 2_000_000:
                text += (
                    f"\n\n<!-- Preview truncated at 2,000,000 characters; "
                    f"full CodeWalker XML was {xml_size:,} bytes. -->\n"
                )
            image, count = _texture_contact_sheet(assets)
            return text, image, count, None

    def export_workspace(
        self, source: str | Path, destination: str | Path, *, edition: str,
    ) -> Path:
        authored = Path(source).expanduser()
        if authored.is_symlink():
            raise ValueError("Native source asset cannot be a symbolic link")
        resolved = authored.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"Native source asset not found: {resolved}")
        if resolved.stat().st_size > MAX_NATIVE_PREVIEW_BYTES:
            raise ValueError("Native source asset exceeds the guarded workspace limit")
        return self.export_workspace_bytes(
            resolved.name, resolved.read_bytes(), destination, edition=edition,
            source_path=resolved,
        )

    def export_workspace_bytes(
        self, name: str, data: bytes, destination: str | Path, *, edition: str,
        source_path: Path | None = None,
    ) -> Path:
        """Export XML, dependencies, and an immutable source snapshot for editing."""
        self._require_patcher()
        safe_name = Path(name).name
        suffix = Path(safe_name).suffix.casefold()
        if not safe_name or suffix not in NATIVE_XML_IMPORT_SUFFIXES:
            raise ValueError(f"Native XML round-trip is not supported for {name}")
        if not data or len(data) > MAX_NATIVE_PREVIEW_BYTES:
            raise ValueError("Native workspace source is empty or exceeds the guarded limit")
        normalized_edition = self._normalize_edition(edition)
        target = Path(destination).expanduser().resolve()
        if target.exists() or target.is_symlink():
            raise ValueError(f"Native workspace destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(
            prefix=f".{target.name}.allin1-stage-", dir=target.parent,
        )).resolve()
        try:
            original_dir = staging / "original"
            edit_dir = staging / "edit"
            assets = edit_dir / "assets"
            original_dir.mkdir()
            assets.mkdir(parents=True)
            source_snapshot = original_dir / safe_name
            source_snapshot.write_bytes(data)
            xml = edit_dir / f"{safe_name}.xml"
            completed = run_hidden(
                [
                    self.patcher, "asset-xml", source_snapshot, xml, assets,
                    "gen9" if normalized_edition == "Enhanced" else "legacy",
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if completed.returncode or not xml.is_file():
                detail = (
                    completed.stderr or completed.stdout or "conversion failed"
                ).strip()
                raise RuntimeError(f"Native XML workspace export failed: {detail}")
            dependencies = self._workspace_files(assets)
            manifest = {
                "schema_version": NATIVE_WORKSPACE_SCHEMA,
                "operation": "native_asset_workspace",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "edition": normalized_edition,
                "source": {
                    "name": safe_name, "suffix": suffix, "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "snapshot": f"original/{safe_name}",
                    "authored_path": str(source_path) if source_path else None,
                },
                "xml": {
                    "path": f"edit/{safe_name}.xml", "size": xml.stat().st_size,
                    "base_sha256": _sha256_file(xml),
                },
                "dependencies": dependencies,
                "safety": {
                    "source_snapshot_immutable": True,
                    "game_write_performed": False,
                    "rebuilt_asset_requires_parse_validation": True,
                },
            }
            _write_json_atomic(staging / "native-workspace.json", manifest)
            staging.replace(target)
            return target
        except Exception:
            if staging.is_dir() and staging.parent == target.parent:
                shutil.rmtree(staging)
            raise

    def build_workspace(
        self, workspace: str | Path, output: str | Path,
    ) -> tuple[Path, Path]:
        """Rebuild and reparse one manifest-backed native editing workspace."""
        self._require_patcher()
        authored_root = Path(workspace).expanduser()
        if authored_root.is_symlink():
            raise ValueError("Native workspace cannot be a symbolic link")
        root = authored_root.resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Native workspace not found: {root}")
        manifest_path = root / "native-workspace.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise ValueError("Native workspace manifest is missing or unsafe")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid native workspace manifest: {exc}") from exc
        if not isinstance(manifest, dict) or manifest.get(
            "schema_version"
        ) != NATIVE_WORKSPACE_SCHEMA or manifest.get(
            "operation"
        ) != "native_asset_workspace":
            raise ValueError("Unsupported native workspace manifest")
        source = manifest.get("source")
        xml_meta = manifest.get("xml")
        if not isinstance(source, dict) or not isinstance(xml_meta, dict):
            raise ValueError("Native workspace is missing source or XML metadata")
        source_snapshot = self._workspace_member(root, source.get("snapshot"), "source")
        xml = self._workspace_member(root, xml_meta.get("path"), "XML")
        assets = (root / "edit" / "assets").resolve()
        if not assets.is_relative_to(root) or not assets.is_dir() or assets.is_symlink():
            raise ValueError("Native workspace asset folder is missing or unsafe")
        if not source_snapshot.is_file() or source_snapshot.is_symlink():
            raise ValueError("Native workspace source snapshot is missing or unsafe")
        if not xml.is_file() or xml.is_symlink():
            raise ValueError("Native workspace XML is missing or unsafe")
        if not 0 < xml.stat().st_size <= MAX_NATIVE_WORKSPACE_BYTES:
            raise ValueError("Native workspace XML is empty or exceeds guarded limits")
        source_name = source.get("name")
        expected_suffix = str(source.get("suffix", "")).casefold()
        if (
            not isinstance(source_name, str) or not source_name
            or Path(source_name).name != source_name
            or source_snapshot.name != source_name
            or Path(source_name).suffix.casefold() != expected_suffix
        ):
            raise ValueError("Native workspace source identity was modified")
        source_size = source.get("size")
        source_sha256 = source.get("sha256")
        if (
            not isinstance(source_size, int) or isinstance(source_size, bool)
            or not isinstance(source_sha256, str) or len(source_sha256) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in source_sha256)
        ):
            raise ValueError("Native workspace source metadata is malformed")
        if source_snapshot.stat().st_size != source_size or _sha256_file(
            source_snapshot
        ) != source_sha256:
            raise ValueError("Native workspace source snapshot was modified")
        if expected_suffix not in NATIVE_XML_IMPORT_SUFFIXES:
            raise ValueError("Native workspace has an unsupported output format")
        edition = self._normalize_edition(str(manifest.get("edition", "")))
        self._workspace_files(assets)

        destination = Path(output).expanduser().resolve()
        if destination.suffix.casefold() != expected_suffix:
            raise ValueError(
                f"Native workspace output must retain the {expected_suffix} extension"
            )
        report = destination.with_name(f"{destination.name}.allin1.json")
        if (
            destination.exists() or destination.is_symlink()
            or report.exists() or report.is_symlink()
        ):
            raise ValueError(f"Native workspace output already exists: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="allin1-native-build-", dir=destination.parent,
        ) as temporary:
            stage_root = Path(temporary)
            staged = stage_root / destination.name
            completed = run_hidden(
                [
                    self.patcher, "asset-from-xml", xml, staged, assets,
                    "gen9" if edition == "Enhanced" else "legacy", source_snapshot,
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if completed.returncode or not staged.is_file():
                detail = (
                    completed.stderr or completed.stdout or "rebuild failed"
                ).strip()
                raise RuntimeError(f"Native XML workspace rebuild failed: {detail}")
            if not 0 < staged.stat().st_size <= MAX_NATIVE_PREVIEW_BYTES:
                raise RuntimeError("Rebuilt native asset is empty or exceeds the safe limit")
            validation_xml = stage_root / f"{destination.name}.validated.xml"
            validation_assets = stage_root / "validated-assets"
            validation = run_hidden(
                [
                    self.patcher, "asset-xml", staged, validation_xml,
                    validation_assets,
                    "gen9" if edition == "Enhanced" else "legacy",
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if validation.returncode or not validation_xml.is_file():
                detail = (
                    validation.stderr or validation.stdout or "parse validation failed"
                ).strip()
                raise RuntimeError(f"Rebuilt native asset failed validation: {detail}")
            result = {
                "schema_version": 1,
                "operation": "native_asset_workspace_build",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "workspace": str(root), "edition": edition,
                "source_sha256": source["sha256"],
                "edited_xml_sha256": _sha256_file(xml),
                "output": {
                    "path": str(destination), "size": staged.stat().st_size,
                    "sha256": _sha256_file(staged),
                },
                "validation": {
                    "reparsed": True,
                    "xml_sha256": _sha256_file(validation_xml),
                    "dependency_count": len(self._workspace_files(validation_assets)),
                },
            }
            temporary_report = stage_root / "build-report.json"
            _write_json_atomic(temporary_report, result)
            published: list[Path] = []
            try:
                # Keep both publications inside the destination filesystem. On the
                # Windows desktop target rename refuses a raced destination; cleanup
                # below also prevents a half-published asset/report pair.
                temporary_report.rename(report)
                published.append(report)
                staged.rename(destination)
                published.append(destination)
            except Exception:
                for path in reversed(published):
                    try:
                        path.unlink()
                    except OSError:
                        pass
                raise
        return destination, report

    def _require_patcher(self) -> None:
        if not self.patcher.is_file():
            raise FileNotFoundError(
                "RpfPatcher is not built; run runtools.ps1 before native authoring"
            )

    @staticmethod
    def _normalize_edition(edition: str) -> str:
        normalized = str(edition).strip().casefold()
        if normalized in {"enhanced", "gen9"}:
            return "Enhanced"
        if normalized == "legacy":
            return "Legacy"
        raise ValueError("Native workspace edition must be Legacy or Enhanced")

    @staticmethod
    def _workspace_member(root: Path, value: object, label: str) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Native workspace {label} path is missing")
        relative = PurePosixPath(value.replace("\\", "/"))
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError(f"Native workspace {label} path is unsafe")
        resolved = root.joinpath(*relative.parts).resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"Native workspace {label} path escapes its root")
        return resolved

    @staticmethod
    def _workspace_files(root: Path) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        total = 0
        if not root.is_dir():
            return records
        for path in sorted(root.rglob("*"), key=lambda item: str(item).casefold()):
            if path.is_symlink():
                raise ValueError(f"Native workspace contains a symbolic link: {path}")
            resolved = path.resolve()
            if not resolved.is_relative_to(root.resolve()):
                raise ValueError(f"Native workspace dependency escapes its root: {path}")
            if not path.is_file():
                continue
            relative = path.relative_to(root).as_posix()
            size = path.stat().st_size
            total += size
            records.append({
                "path": relative, "size": size, "sha256": _sha256_file(path),
            })
            if len(records) > MAX_NATIVE_WORKSPACE_FILES or total > (
                MAX_NATIVE_WORKSPACE_BYTES
            ):
                raise ValueError("Native workspace dependencies exceed guarded limits")
        return records


def native_preview_limit(name: str, size: int) -> int:
    """Return the bounded read size appropriate for a package member."""
    if Path(name).suffix.casefold() not in NATIVE_ASSET_SUFFIXES:
        return min(size + 1, 8 * 1024 * 1024)
    return min(size + 1, MAX_NATIVE_PREVIEW_BYTES)
