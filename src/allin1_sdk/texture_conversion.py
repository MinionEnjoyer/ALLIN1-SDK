"""Bounded 2D DDS encoding with explicit format and regenerated mip chains."""
from __future__ import annotations

import io
import struct
from pathlib import Path

from PIL import Image

from allin1_sdk.texture_workspace import DdsMetadata, inspect_dds

OUTPUT_FORMATS = {"RGBA8": "D3DFMT_A8R8G8B8", "DXT1": "D3DFMT_DXT1", "DXT3": "D3DFMT_DXT3", "DXT5": "D3DFMT_DXT5"}


def conversion_metadata(width, height, output_format, mip_levels):
    if not isinstance(output_format, str) or output_format not in OUTPUT_FORMATS:
        raise ValueError("Output format must be RGBA8, DXT1, DXT3, or DXT5")
    maximum = max(width, height).bit_length()
    if not isinstance(mip_levels, int) or isinstance(mip_levels, bool) or not 1 <= mip_levels <= maximum:
        raise ValueError(f"Mip count must be between 1 and {maximum} for this texture")
    return DdsMetadata(width, height, mip_levels, OUTPUT_FORMATS[output_format])


def convert_dds(source: Path, destination: Path, output_format: str, mip_levels: int):
    metadata = inspect_dds(source)
    expected = conversion_metadata(metadata.width, metadata.height, output_format, mip_levels)
    with source.open("rb") as stream:
        source_header = stream.read(148)
    if struct.unpack_from("<I", source_header, 24)[0] > 1 or struct.unpack_from("<I", source_header, 112)[0] & (0xFE00 | 0x200000):
        raise ValueError("Texture conversion supports 2D DDS only, not volume/cubemap textures")
    if source_header[84:88] == b"DX10" and (struct.unpack_from("<I", source_header, 132)[0] != 3 or struct.unpack_from("<I", source_header, 136)[0] & 4 or struct.unpack_from("<I", source_header, 140)[0] != 1):
        raise ValueError("Texture conversion does not support DDS arrays or cubemaps")
    with Image.open(source) as opened:
        if opened.size != (metadata.width, metadata.height):
            raise ValueError("DDS dimensions changed before conversion")
        mip = opened.convert("RGB" if output_format == "DXT1" else "RGBA")
    try:
        with destination.open("xb") as output:
            for index in range(mip_levels):
                with io.BytesIO() as buffer:
                    mip.save(buffer, format="DDS", **({"pixel_format": output_format} if output_format != "RGBA8" else {}))
                    encoded = buffer.getvalue()
                if index == 0:
                    header = bytearray(encoded[:128])
                    # DDSD_MIPMAPCOUNT; DDSCAPS_COMPLEX | DDSCAPS_MIPMAP.
                    struct.pack_into("<I", header, 28, mip_levels)
                    if mip_levels > 1:
                        struct.pack_into("<I", header, 8, struct.unpack_from("<I", header, 8)[0] | 0x20000)
                        struct.pack_into("<I", header, 108, struct.unpack_from("<I", header, 108)[0] | 0x400008)
                    if output_format != "RGBA8":
                        struct.pack_into("<I", header, 20, len(encoded) - 128)
                    output.write(header)
                output.write(encoded[128:])
                if index + 1 < mip_levels:
                    smaller = mip.resize((max(1, mip.width // 2), max(1, mip.height // 2)), Image.Resampling.BOX)
                    mip.close()
                    mip = smaller
    finally:
        mip.close()
    if inspect_dds(destination) != expected:
        raise ValueError("Encoded DDS metadata does not match the requested conversion")
    return expected
