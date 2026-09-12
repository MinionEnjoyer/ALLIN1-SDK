"""Bounded RPF-aware package materialization with exact source provenance.

The input is never edited. Native archives expand only with explicit matching
decoder context; unavailable archives remain visible as unexpanded inputs.
"""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import shutil
import tempfile

from allin1_sdk.paths import project_root
from allin1_sdk.release_paths import contained
from allin1_sdk.rpf_tools import RpfExplorerService, _content_fingerprint
from allin1_sdk.workspace_desktop import _inventory, digest, file_hash, path


MAX_INPUT_FILES = 1000
MAX_INPUT_BYTES = 2 * 1024**3
MAX_OUTER_ARCHIVES = 32
_COPY_CHUNK_BYTES = 1024 * 1024


def _copy_inventory_file(source: Path, target: Path, expected_size: int,
                         expected_sha256: str) -> None:
    """Copy one inventoried file without allowing a later growth race to spill.

    ``shutil.copyfile`` only notices a changed source after it has copied it.
    Package intake has already committed to the original size and hash, so
    stream exactly that many bytes, probe for one more byte, and bind the copy
    to the inventory fingerprint before it can be inspected.
    """
    copied = 0
    digest = hashlib.sha256()
    with source.open("rb") as incoming, target.open("xb") as outgoing:
        while copied < expected_size:
            chunk = incoming.read(min(_COPY_CHUNK_BYTES, expected_size - copied))
            if not chunk:
                raise ValueError("Package source size changed while staging")
            copied += len(chunk)
            digest.update(chunk)
            outgoing.write(chunk)
        if incoming.read(1):
            raise ValueError("Package source size changed while staging")
    if digest.hexdigest() != expected_sha256:
        raise ValueError("Package source content changed while staging")


@contextmanager
def materialize(source, *, edition=None, gta_path=None, force_copy=False):
    source=path(str(source))
    directory=source.is_dir()
    if not directory and source.suffix.casefold()!=".rpf":
        raise ValueError("Choose a package folder or a loose RPF archive")
    before=_inventory(source,limit=MAX_INPUT_FILES,size_limit=MAX_INPUT_BYTES) if directory else {source.name:file_hash(source)}
    if not directory and source.stat().st_size>MAX_INPUT_BYTES:
        raise ValueError("Archive intake exceeds 2 GiB")
    archive_names=[name for name in before if name.casefold().endswith(".rpf")]
    if not archive_names and directory and not force_copy:
        yield source,{"source_kind":"package_folder","source_sha256":digest(before),"source_inventory":before,"archives":[],"members":[]}
        if _inventory(source,limit=1000)!=before:
            raise ValueError("Package changed during inspection")
        return
    with tempfile.TemporaryDirectory(prefix="allin1-package-intake-") as temporary:
        root=Path(temporary)/"package";root.mkdir()
        input_files = {
            name: contained(source,name) if directory else source
            for name in before
        }
        input_sizes = {}
        for name, input_file in input_files.items():
            if not input_file.is_file():
                raise ValueError(f"Package input is not a regular file: {name}")
            input_sizes[name] = input_file.stat().st_size
        source_bytes = sum(input_sizes.values())
        if source_bytes > MAX_INPUT_BYTES:
            raise ValueError("Package intake exceeds 2 GiB")
        if shutil.disk_usage(root).free < source_bytes+64*1024**2:
            raise ValueError("Insufficient staging space for the package inspection copy")
        for name in before:
            target=contained(root,name);target.parent.mkdir(parents=True,exist_ok=True)
            _copy_inventory_file(input_files[name],target,input_sizes[name],before[name])
        if _inventory(root,limit=MAX_INPUT_FILES,size_limit=MAX_INPUT_BYTES)!=before:
            raise ValueError("Package changed while making the inspection copy")
        archives=[];members=[];logical_bytes=0
        game=path(str(gta_path)) if gta_path else None
        for number,name in enumerate(archive_names):
            archive=contained(root,name)
            record={"path":name,"sha256":before[name],"status":"unexpanded"}
            archives.append(record)
            produced=False
            try:
                if number>=MAX_OUTER_ARCHIVES:
                    raise ValueError("Archive intake exceeds 32 outer archives")
                if not game or edition not in {"Legacy","Enhanced"}:
                    raise ValueError("RPF expansion requires explicit edition and matching decoder installation")
                service=RpfExplorerService(project_root(),game)
                index=service.index(archive)
                if index.edition.casefold()!=edition.casefold():
                    raise ValueError("Archive decoder context does not match the selected edition")
                if index.warnings:
                    raise ValueError("Archive index is incomplete: "+"; ".join(index.warnings)[:300])
                leaves=[entry for entry in index.entries if entry.kind not in {"archive","directory"}]
                if (len(leaves)+len(members)+len(before)>MAX_INPUT_FILES
                        or sum(entry.size for entry in leaves)+logical_bytes>MAX_INPUT_BYTES):
                    raise ValueError("Expanded package exceeds 1,000 files or 2 GiB")
                if shutil.disk_usage(root).free < sum(entry.size for entry in leaves)+64*1024**2:
                    raise ValueError("Insufficient staging space for expanded archive payloads")
                expanded_name=name+".source"
                expanded=contained(root,expanded_name)
                if expanded.exists():
                    raise ValueError("Expanded archive path collides with a supplied package path")
                _,report=service.extract_authoring_tree(index,expanded)
                produced=True
                for item in report["files"]:
                    logical=expanded_name+"/"+item["relative_path"]
                    payload=contained(root,logical)
                    logical_bytes+=payload.stat().st_size
                    if logical_bytes>MAX_INPUT_BYTES:
                        raise ValueError("Extracted payload bytes exceed 2 GiB")
                    try:
                        fingerprint=_content_fingerprint(payload)
                        content={"content_sha256":fingerprint["canonical_sha256"],"content_hash_mode":fingerprint["mode"]}
                    except (OSError,ValueError) as exc:
                        content={"content_hash_mode":"unavailable","content_hash_reason":str(exc)[:200]}
                    members.append({"path":logical,"container":name,"container_sha256":before[name],
                        "entry_id":item["archive_path"]+"::"+item["entry_path"],"archive_path":item["archive_path"],"entry_path":item["entry_path"],
                        "sha256":item["sha256"],"sha256_kind":"extracted_bytes",**content})
                # Only the private copy is removed; the original container is
                # retained at its authored path and fingerprinted in provenance.
                archive.unlink()
                record.update(status="expanded",members=len(leaves),nested_archives=len(index.archives)-1)
            except (OSError,RuntimeError,ValueError) as exc:
                record["reason"]=str(exc)[:500]
                # A failed extraction must never contribute partial members.
                if record["status"]!="expanded" and produced:
                    raise ValueError("Archive expansion exceeded its bounds after extraction") from exc
        _inventory(root,limit=MAX_INPUT_FILES,size_limit=MAX_INPUT_BYTES)
        provenance={"source_kind":"package_folder" if directory else "rpf_archive","source_sha256":digest(before) if directory else before[source.name],
            "source_inventory":before,"archives":archives,"members":members}
        yield root,provenance
        after=_inventory(source,limit=MAX_INPUT_FILES,size_limit=MAX_INPUT_BYTES) if directory else {source.name:file_hash(source)}
        if after!=before:
            raise ValueError("Original package/archive changed during inspection")
