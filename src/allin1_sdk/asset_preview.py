"""Bounded, read-only package previews for desktop and other presentation layers."""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from allin1_sdk.addon_importer import (
    PackageAssetReader,
    asset_category,
    decode_text_preview,
    hex_preview,
)
from allin1_sdk.native_assets import (
    MAX_NATIVE_PREVIEW_BYTES,
    NATIVE_ASSET_SUFFIXES,
    NativeAssetInspector,
    native_preview_limit,
)


MAX_TEXT_CHARS = 32_000
MAX_IMAGE_PIXELS = 40_000_000
MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
MAX_CACHE_FILES = 64
MAX_CACHE_BYTES = 256 * 1024 * 1024


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _bounded_text(value: str) -> tuple[str, bool]:
    if len(value) <= MAX_TEXT_CHARS:
        return value, False
    return value[:MAX_TEXT_CHARS], True


class PreviewArtifactStore:
    """Write only normalized previews into one broker-owned cache directory."""

    def __init__(self, root: str | Path) -> None:
        authored = Path(root).expanduser()
        if authored.exists() and authored.is_symlink():
            raise ValueError("Preview artifact root cannot be a symbolic link")
        authored.mkdir(parents=True, exist_ok=True)
        self.root = authored.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Preview artifact root must be a directory")

    def write_png(self, data: bytes) -> dict[str, Any]:
        if not data or len(data) > MAX_ARTIFACT_BYTES:
            raise ValueError("Rendered preview exceeds the artifact size limit")
        digest = _sha256(data)
        destination = self.root / f"{digest}.png"
        if destination.exists() and destination.is_symlink():
            raise ValueError("Preview artifact destination cannot be a symbolic link")
        handle, temporary_name = tempfile.mkstemp(
            prefix=".allin1-preview-", suffix=".tmp", dir=self.root,
        )
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            Path(temporary_name).replace(destination)
        finally:
            temporary = Path(temporary_name)
            if temporary.exists():
                temporary.unlink()
        if destination.resolve(strict=True).parent != self.root:
            raise ValueError("Preview artifact escaped its cache directory")
        self._prune(keep=destination)
        return {
            "path": str(destination),
            "sha256": digest,
            "size": len(data),
            "media_type": "image/png",
        }

    def _prune(self, *, keep: Path) -> None:
        files = sorted(
            (
                item for item in self.root.glob("*.png")
                if item.is_file() and not item.is_symlink()
            ),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        total = 0
        retained = 0
        for item in files:
            size = item.stat().st_size
            should_keep = (
                item == keep
                or (retained < MAX_CACHE_FILES and total + size <= MAX_CACHE_BYTES)
            )
            if should_keep:
                retained += 1
                total += size
            else:
                item.unlink(missing_ok=True)


class AssetPreviewService:
    """Turn one validated package member into bounded UI-safe preview evidence."""

    def __init__(
        self, project_root: str | Path, *, gta_path: str | Path | None = None,
        artifact_root: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve(strict=True)
        self.gta_path = (
            Path(gta_path).expanduser().resolve(strict=True)
            if gta_path is not None else None
        )
        configured = artifact_root or os.environ.get("ALLIN1_PREVIEW_DIR", "").strip()
        self.artifacts = PreviewArtifactStore(configured) if configured else None

    def preview(
        self, source: str | Path, entry_path: str, *, edition: str = "Enhanced",
    ) -> dict[str, Any]:
        source_path = Path(source).expanduser().resolve(strict=True)
        reader = PackageAssetReader(
            source_path, project_root=self.project_root, gta_path=self.gta_path,
        )
        limit = native_preview_limit(entry_path, MAX_NATIVE_PREVIEW_BYTES)
        content = reader.read(entry_path, limit=limit)
        suffix = Path(content.path).suffix.casefold()
        result: dict[str, Any] = {
            "source": str(source_path),
            "path": content.path,
            "name": Path(content.path).name,
            "category": asset_category(content.path),
            "preview_kind": content.preview_kind,
            "display_kind": "metadata",
            "size": content.size,
            "bytes_read": len(content.data),
            "truncated": content.truncated,
            "sha256": content.sha256,
            "text": None,
            "text_truncated": False,
            "artifact": None,
            "metadata": {},
            "warnings": [],
        }
        if content.preview_kind == "image" and not content.truncated:
            self._image_preview(result, content.data)
        elif content.preview_kind == "text":
            text, clipped = _bounded_text(decode_text_preview(content.data))
            result.update({
                "display_kind": "text",
                "text": text or "(empty file)",
                "text_truncated": clipped or content.truncated,
            })
        elif suffix in NATIVE_ASSET_SUFFIXES:
            # PackageAssetReader has already resolved the exact virtual member.
            # Keep that identity in the result; only the decoder's temporary
            # filename is a leaf. A nested ID's "::" is not a Windows filename.
            decoder_name = Path(content.path.rsplit("::", 1)[-1]).name
            self._native_preview(
                result, decoder_name, content.data,
                edition=edition, truncated=content.truncated,
            )
        else:
            text, clipped = _bounded_text(
                "Binary asset. The viewer displays a bounded header and never "
                "executes or rewrites package content.\n\nFirst bytes\n\n"
                + hex_preview(content.data)
            )
            result.update({
                "display_kind": "text",
                "text": text,
                "text_truncated": clipped or content.truncated,
            })
        return result

    def _image_preview(self, result: dict[str, Any], data: bytes) -> None:
        try:
            with Image.open(io.BytesIO(data)) as source:
                if source.width * source.height > MAX_IMAGE_PIXELS:
                    raise ValueError("Image preview exceeds the guarded pixel limit")
                normalized = ImageOps.exif_transpose(source).convert("RGBA")
                original = normalized.size
                normalized.thumbnail((1600, 1200), Image.Resampling.LANCZOS)
                rendered_size = normalized.size
                output = io.BytesIO()
                normalized.save(output, format="PNG", optimize=True)
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError, ValueError) as exc:
            result["warnings"] = [f"Image preview unavailable: {exc}"]
            text, clipped = _bounded_text(
                "Image decoding failed. The package bytes were not rendered.\n\n"
                + hex_preview(data)
            )
            result.update({
                "display_kind": "text", "text": text,
                "text_truncated": clipped,
            })
            return
        if self.artifacts is None:
            result["warnings"] = [
                "Preview artifact cache is unavailable; image metadata only."
            ]
            result["metadata"] = {
                "dimensions": f"{original[0]} × {original[1]}",
            }
            return
        artifact = self.artifacts.write_png(output.getvalue())
        artifact.update({"width": rendered_size[0], "height": rendered_size[1]})
        result.update({
            "display_kind": "image",
            "artifact": artifact,
            "metadata": {
                "dimensions": f"{original[0]} × {original[1]}",
                "rendered_dimensions": f"{rendered_size[0]} × {rendered_size[1]}",
            },
        })

    def _native_preview(
        self, result: dict[str, Any], name: str, data: bytes, *,
        edition: str, truncated: bool,
    ) -> None:
        try:
            report = NativeAssetInspector(
                self.project_root, self.gta_path,
            ).inspect_bytes(name, data, edition=edition, truncated=truncated)
        except (OSError, RuntimeError, ValueError) as exc:
            result["warnings"] = [f"Native preview unavailable: {exc}"]
            result.update({
                "display_kind": "text",
                "text": "Native preview unavailable.\n\n" + hex_preview(data),
            })
            return
        text = report.summary()
        if report.structured_text:
            text += "\n\nStructured CodeWalker preview\n\n" + report.structured_text
        elif not report.image_png:
            text += "\n\nFirst bytes\n\n" + hex_preview(data)
        bounded, clipped = _bounded_text(text)
        result.update({
            "display_kind": "text",
            "text": bounded,
            "text_truncated": clipped or truncated,
            "metadata": {
                "format": report.format_name,
                **report.metadata,
            },
            "warnings": list(report.warnings),
        })
        if report.image_png and self.artifacts is not None:
            artifact = self.artifacts.write_png(report.image_png)
            result.update({"display_kind": "image", "artifact": artifact})
