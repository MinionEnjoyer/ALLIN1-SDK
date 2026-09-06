"""Bounded native metadata intake; decoding never certifies a schema."""
import hashlib
from pathlib import Path
import tempfile

from allin1_sdk.native_assets import NativeAssetInspector
from allin1_sdk.paths import project_root

NATIVE_SUFFIXES = (".ymt", ".ytyp", ".ymap", ".ymf", ".pso")
XML_SUFFIXES = (".meta", *(suffix + ".xml" for suffix in NATIVE_SUFFIXES))
MAX_NATIVE_BYTES = 16 * 1024**2
MAX_XML_BYTES = 8 * 1024**2
MAX_DECODED_BYTES = 64 * 1024**2
MAX_NATIVE_FILES = 32


def documents(base, inventory, label, edition, game, add, evidence):
    inspector = NativeAssetInspector(project_root(), game)
    native_count = decoded_total = 0
    with tempfile.TemporaryDirectory(prefix="allin1-metadata-validation-") as temporary:
        for name, checksum in inventory.items():
            native = name.lower().endswith(NATIVE_SUFFIXES)
            if not native and not name.lower().endswith(XML_SUFFIXES):
                continue
            source = f"{label}:{name}"
            row = {"source":source,"source_sha256":checksum,"native":native,
                   "xml_sha256":None,"xml_bytes":None,"status":"not_checked"}
            evidence.append(row)
            try:
                if native:
                    native_count += 1
                    if native_count > MAX_NATIVE_FILES:
                        raise ValueError("Native metadata context exceeds 32 files; narrow the selection")
                    if not edition:
                        raise ValueError("Native metadata decoding requires an explicit edition")
                limit = MAX_NATIVE_BYTES if native else MAX_XML_BYTES
                with (base / name).open("rb") as stream:
                    data = stream.read(limit + 1)
                if len(data) > limit:
                    raise ValueError("Metadata exceeds the bounded input size")
                if hashlib.sha256(data).hexdigest() != checksum:
                    raise ValueError("Metadata changed since input inventory")
                if native:
                    workspace = Path(temporary) / str(native_count)
                    inspector.export_workspace_bytes(Path(name).name,data,workspace,edition=edition)
                    with (workspace / "edit" / (Path(name).name + ".xml")).open("rb") as stream:
                        data = stream.read(MAX_XML_BYTES + 1)
                if len(data) > MAX_XML_BYTES:
                    raise ValueError("Decoded metadata exceeds 8 MiB")
                decoded_total += len(data)
                if decoded_total > MAX_DECODED_BYTES:
                    raise ValueError("Metadata XML context exceeds 64 MiB; narrow the selection")
                row.update(xml_sha256=hashlib.sha256(data).hexdigest(),xml_bytes=len(data),status="xml_available")
                yield name, data, row
            except (ValueError, RuntimeError, OSError) as exc:
                row["reason"] = str(exc)[:500]
                add("metadata","not_checked","native_metadata_unparsed" if native else "metadata_unavailable",source,str(exc))
                if name.lower().endswith(".ymt"):
                    add("textures","not_checked","native_texture_relationships_unparsed",source,"This YMT was not decoded; any contained parent/shared texture context remains unchecked.")
