"""DDS payload and mip-storage evidence; never a claim of measured VRAM usage."""
import hashlib
import io
import struct

from PIL import Image

from allin1_sdk.texture_workspace import inspect_dds
from allin1_sdk.workspace_desktop import path

BLOCK_BYTES = {"D3DFMT_DXT1":8,"D3DFMT_ATI1":8,"D3DFMT_DXT3":16,"D3DFMT_DXT5":16,"D3DFMT_ATI2":16,"D3DFMT_BC7":16}
PIXEL_BYTES = {"D3DFMT_A8R8G8B8":4,"D3DFMT_X8R8G8B8":4,"D3DFMT_A8B8G8R8":4,"D3DFMT_A1R5G5B5":2,"D3DFMT_A8":1,"D3DFMT_L8":1}


def mip_costs(width, height, count, format_name):
    if any(isinstance(v,bool) or not isinstance(v,int) for v in (width,height,count)) or not 1<=width<=16384 or not 1<=height<=16384 or not 1<=count<=max(width,height).bit_length():
        raise ValueError("Invalid texture dimensions or mip-chain length")
    if format_name not in BLOCK_BYTES and format_name not in PIXEL_BYTES:
        raise ValueError("Texture storage format is not supported")
    rows=[]
    for level in range(count):
        size=((width+3)//4)*((height+3)//4)*BLOCK_BYTES[format_name] if format_name in BLOCK_BYTES else width*height*PIXEL_BYTES[format_name]
        rows.append({"level":level,"width":width,"height":height,"storage_bytes":size})
        width,height=max(1,width//2),max(1,height//2)
    return rows


def inspect(source):
    file=path(str(source))
    if file.stat().st_size>128*1024**2:
        raise ValueError("DDS payload exceeds 128 MiB")
    with file.open("rb") as stream:
        data=stream.read(128*1024**2+1)
    if len(data)>128*1024**2:
        raise ValueError("DDS payload grew beyond 128 MiB")
    metadata=inspect_dds(file)
    if data[:4]!=b"DDS " or len(data)<128:
        raise ValueError("Truncated DDS header")
    if struct.unpack_from("<I",data,24)[0]>1 or struct.unpack_from("<I",data,112)[0] & (0xFE00|0x200000):
        raise NotImplementedError("Cube/volume texture residency and payload layouts are not validated")
    header_size=148 if data[84:88]==b"DX10" else 128
    if header_size==148 and (len(data)<148 or struct.unpack_from("<I",data,132)[0]!=3 or struct.unpack_from("<I",data,136)[0]&4 or struct.unpack_from("<I",data,140)[0]!=1):
        raise NotImplementedError("DDS arrays and non-2D DX10 resources are not validated")
    rows=mip_costs(metadata.width,metadata.height,metadata.mip_levels,metadata.format)
    required=header_size+sum(row["storage_bytes"] for row in rows)
    if len(data)<required:
        raise ValueError(f"DDS mip payload is truncated: expected at least {required} bytes, found {len(data)}")
    # Padded uncompressed scanlines need a separate layout model. Do not claim
    # tight-storage mip verification for an incompatible pitch declaration.
    if metadata.format in PIXEL_BYTES and struct.unpack_from("<I",data,8)[0]&8:
        pitch=struct.unpack_from("<I",data,20)[0]
        if pitch!=metadata.width*PIXEL_BYTES[metadata.format]:
            raise NotImplementedError("Padded DDS rows are not covered by the tight-storage estimate")
    if metadata.width*metadata.height>16_777_216:
        raise NotImplementedError("All-mip pixel decoding exceeds the 16-million-pixel validation bound")
    offset=header_size
    for row in rows:
        header=bytearray(data[:header_size])
        struct.pack_into("<II",header,12,row["height"],row["width"])
        struct.pack_into("<I",header,28,1)
        struct.pack_into("<I",header,20,row["storage_bytes"] if metadata.format in BLOCK_BYTES else row["width"]*PIXEL_BYTES[metadata.format])
        block=data[offset:offset+row["storage_bytes"]]
        try:
            with Image.open(io.BytesIO(header+block)) as image:
                image.load()
                if image.size!=(row["width"],row["height"]):
                    raise ValueError("DDS decoded mip dimensions disagree with its header")
        except NotImplementedError as exc:
            raise NotImplementedError(f"DDS mip decoder does not support {metadata.format}") from exc
        offset+=row["storage_bytes"]
    with file.open("rb") as stream:
        if hashlib.file_digest(stream,"sha256").hexdigest()!=hashlib.sha256(data).hexdigest():
            raise ValueError("DDS changed during validation")
    return {"sha256":hashlib.sha256(data).hexdigest(),"format":metadata.format,"width":metadata.width,"height":metadata.height,
            "mip_levels":metadata.mip_levels,"mips":rows,"storage_bytes":required-header_size,"file_bytes":len(data),"trailing_bytes":len(data)-required,
            "full_chain":metadata.mip_levels==max(metadata.width,metadata.height).bit_length(),"all_mips_decoded":True,
            "memory_scope":"Tightly packed 2D mip storage only; excludes resource/page alignment, streaming residency, driver copies and runtime allocations."}
