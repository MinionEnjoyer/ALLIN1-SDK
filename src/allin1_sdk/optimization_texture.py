"""Measured, role-gated DDS candidates; no inferred material or quality approval."""
import base64
import io
import hashlib
import struct
from pathlib import Path
import tempfile

from PIL import Image, ImageChops, ImageStat

from allin1_sdk.texture_conversion import convert_dds
from allin1_sdk.texture_validation import inspect


def _thumbnail(image):
    preview = image.copy()
    try:
        preview.thumbnail((64, 64), Image.Resampling.LANCZOS)
        with io.BytesIO() as stream:
            preview.save(stream, format="PNG")
            return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode("ascii")
    finally:
        preview.close()


def _mip(source, metadata, level):
    if level >= metadata["mip_levels"]:
        return None
    with Path(source).open("rb") as stream:
        data = stream.read(128*1024**2+1)
    if len(data)>128*1024**2:
        raise ValueError("DDS grew beyond pixel-inspection limit")
    if hashlib.sha256(data).hexdigest() != metadata["sha256"]:
        raise ValueError("DDS changed before pixel-region inspection")
    header_size = 148 if data[84:88] == b"DX10" else 128
    row = metadata["mips"][level]
    offset = header_size + sum(item["storage_bytes"] for item in metadata["mips"][:level])
    header = bytearray(data[:header_size])
    struct.pack_into("<II", header, 12, row["height"], row["width"])
    struct.pack_into("<I", header, 28, 1)
    from allin1_sdk.texture_validation import BLOCK_BYTES, PIXEL_BYTES
    struct.pack_into("<I", header, 20, row["storage_bytes"] if metadata["format"] in BLOCK_BYTES else row["width"] * PIXEL_BYTES[metadata["format"]])
    with Image.open(io.BytesIO(header + data[offset:offset+row["storage_bytes"]])) as opened:
        return opened.convert("RGBA")


def region_preview(source, destination, before, after, region):
    if not isinstance(region, dict) or set(region) != {"mip", "x", "y", "channel"} or any(type(region[key]) is not int or region[key] < 0 for key in ("mip", "x", "y")) or region["channel"] not in {"rgba", "rgb", "alpha"}:
        raise ValueError("Choose an integer mip/region origin and RGBA, RGB or alpha channel")
    mip, x, y = (region[key] for key in ("mip", "x", "y"))
    if mip >= max(before["mip_levels"], after["mip_levels"]):
        raise ValueError("Selected mip does not exist in either texture")
    width, height = max(1, before["width"] >> mip), max(1, before["height"] >> mip)
    if x >= width or y >= height:
        raise ValueError("Pixel-region origin is outside this mip")
    box = (x, y, min(width, x+64), min(height, y+64))
    images = []
    try:
        for source_file, metadata in ((source, before), (destination, after)):
            image = _mip(source_file, metadata, mip)
            if image is None:
                images.append(None)
                continue
            try:
                crop = image.crop(box)
            finally:
                image.close()
            if region["channel"] == "alpha":
                alpha = crop.getchannel("A");crop.close();crop = alpha
            elif region["channel"] == "rgb":
                rgb = crop.convert("RGB");crop.close();crop = rgb
            images.append(crop)
        difference = ImageChops.difference(*images) if all(image is not None for image in images) else None
        if difference is not None and difference.mode == "RGBA":
            rgb = difference.convert("RGB");difference.close();difference = rgb
        images.append(difference)
        return {**region, "width": box[2]-x, "height": box[3]-y, "mip_width": width, "mip_height": height,
                "before": _thumbnail(images[0]) if images[0] is not None else None,
                "after": _thumbnail(images[1]) if images[1] is not None else None,
                "difference": _thumbnail(difference) if difference is not None else None,
                "scope": "Exact decoded pixels at the selected mip and coordinates; no downsampling. Difference shows absolute byte error without amplification (RGB for RGBA mode; select alpha to inspect alpha error). Missing source/candidate mips are not synthesized."}
    finally:
        for image in images:
            if image is not None: image.close()


def candidate(source, destination, settings, *, previews=True, region=None):
    """Create an exclusive candidate; the caller owns staging and publication.

    The source has already been decoded at every mip before conversion. Lossy
    color changes require an explicit material role; normal/mask/data textures
    must use a semantic-aware encoder instead of this color filter.
    """
    if not isinstance(settings, dict) or set(settings) != {"format", "mips", "role"}:
        raise ValueError("Choose an explicit texture format, mip count and material role")
    if settings["role"] != "color":
        raise ValueError("Only explicitly identified color textures use this candidate encoder; normal, mask and unknown material roles are not inferred")
    source, destination = Path(source), Path(destination)
    before = inspect(source)
    with Image.open(source) as opened:
        original = opened.convert("RGBA")
    try:
        alpha_range = original.getextrema()[3]
        if settings["format"] == "DXT1" and alpha_range != (255, 255):
            raise ValueError("DXT1 candidate would discard nonopaque alpha; select RGBA8, DXT3 or DXT5")
        convert_dds(source, destination, settings["format"], settings["mips"])
        after = inspect(destination)
        with Image.open(destination) as opened:
            optimized = opened.convert("RGBA")
        try:
            difference = ImageChops.difference(original, optimized)
            try:
                stats = ImageStat.Stat(difference)
                metrics = {"mean_absolute_error_rgba": stats.mean,
                           "root_mean_square_error_rgba": stats.rms,
                           "maximum_absolute_error_rgba": [pair[1] for pair in difference.getextrema()]}
            finally:
                difference.close()
            evidence = {"before": before, "after": after, "settings": settings,
                        "storage_delta_bytes": after["storage_bytes"]-before["storage_bytes"],
                        "file_delta_bytes": after["file_bytes"]-before["file_bytes"],
                        "quality": metrics, "source_alpha_range": list(alpha_range),
                        "quality_scope": "Top-mip decoded RGBA byte-space errors, not perceptual, linear-light or in-game quality approval. Lower mips are regenerated with BOX filtering; authored lower-mip detail is not preserved.",
                        "memory_scope": "Tightly packed 2D mip-chain storage, not measured GPU residency, streaming behavior or driver allocations.",
                        "runtime_status": "not_tested"}
            if previews:
                evidence["preview_before"] = _thumbnail(original)
                evidence["preview_after"] = _thumbnail(optimized)
                if region is not None:
                    evidence["preview_region"] = region_preview(source, destination, before, after, region)
        finally:
            optimized.close()
    finally:
        original.close()
    if inspect(source)["sha256"] != before["sha256"]:
        raise ValueError("Texture source changed during candidate generation")
    return evidence


def preview(source, settings):
    with tempfile.TemporaryDirectory(prefix="allin1-texture-candidate-") as temporary:
        return candidate(source, Path(temporary)/"candidate.dds", settings)
