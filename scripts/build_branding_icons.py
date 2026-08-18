"""Build the mixed-detail Windows icon used by the desktop SDK."""

from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
ICON_SIZES = (16, 24, 32, 40, 48, 64, 128, 256)


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
    if size <= 24:
        # Gradients turn to indistinct gray at title-bar scale. Collapse them
        # into the brand's three strongest values while retaining alpha edges.
        pixels = []
        for red, green, blue, alpha in image.get_flattened_data():
            if green > (red * 1.2) and green > (blue * 1.5):
                pixels.append((112, 255, 0, alpha))
            elif max(red, green, blue) >= 112:
                pixels.append((248, 250, 249, alpha))
            else:
                pixels.append((4, 9, 7, alpha))
        image.putdata(pixels)
    frame = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    frame.alpha_composite(image, ((size - image.width) // 2, (size - image.height) // 2))
    return frame


def build_favicon(output: Path | None = None) -> Path:
    """Use a bold A1 at title-bar sizes and the full SDK badge elsewhere."""
    output = output or ASSETS / "favicon.ico"
    with Image.open(ASSETS / "ALLIN1-icon.png") as opened:
        compact = opened.convert("RGBA")
    with Image.open(ASSETS / "ALLIN1_SDK.png") as opened:
        full = opened.convert("RGBA")

    payloads: list[tuple[int, bytes]] = []
    for size in ICON_SIZES:
        source = compact if size <= 24 else full
        stream = io.BytesIO()
        _render(source, size).save(stream, format="PNG", optimize=True)
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
