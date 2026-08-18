"""Build the Windows application icon used by the desktop SDK."""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ICON_SIZES = (
    16, 20, 24, 28, 32, 36, 40, 48,
    56, 64, 72, 80, 96, 128, 256,
)


def _render(source: Image.Image, size: int) -> Image.Image:
    image = source.convert("RGBA")
    bounds = image.getchannel("A").getbbox()
    if bounds:
        image = image.crop(bounds)
    inset = 1 if size <= 40 else max(2, round(size * 0.025))
    limit = size - (2 * inset)
    image.thumbnail((limit, limit), Image.Resampling.LANCZOS)
    if size <= 48:
        image = image.filter(ImageFilter.UnsharpMask(radius=0.6, percent=180, threshold=1))
    frame = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    frame.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
    return frame


def build_favicon(output: Path | None = None) -> Path:
    """Build the supplied SDK badge at every supported Windows shell size."""
    output = output or ASSETS / "favicon.ico"
    with Image.open(ASSETS / "ALLIN1_SDK.png") as opened:
        sdk_badge = opened.convert("RGBA")

    payloads: list[tuple[int, bytes]] = []
    for size in ICON_SIZES:
        stream = io.BytesIO()
        _render(sdk_badge, size).save(stream, format="PNG", optimize=True)
        payloads.append((size, stream.getvalue()))

    directory_size = 6 + (16 * len(payloads))
    offset = directory_size
    entries: list[bytes] = []
    images: list[bytes] = []
    for size, payload in payloads:
        dimension = 0 if size == 256 else size
        entries.append(struct.pack(
            "<BBBBHHII", dimension, dimension, 0, 0, 1, 32,
            len(payload), offset,
        ))
        images.append(payload)
        offset += len(payload)
    output.write_bytes(
        struct.pack("<HHH", 0, 1, len(payloads)) + b"".join(entries) + b"".join(images)
    )
    return output


if __name__ == "__main__":
    print(build_favicon())
