"""Bounded RPF-aware package materialization with exact source provenance.

The input is never edited. Native archives expand only with explicit matching
decoder context; unavailable archives remain visible as unexpanded inputs.
"""
from contextlib import contextmanager
from pathlib import Path
import shutil
import tempfile

from allin1_sdk.paths import project_root
from allin1_sdk.release_paths import contained
from allin1_sdk.rpf_tools import RpfExplorerService, _content_fingerprint
from allin1_sdk.workspace_desktop import _inventory, digest, file_hash, path


@contextmanager
def materialize(source, *, edition=None, gta_path=None, force_copy=False):
    source=path(str(source))
    directory=source.is_dir()
    if not directory and source.suffix.casefold()!=".rpf":
        raise ValueError("Choose a package folder or a loose RPF archive")
    before=_inventory(source,limit=1000) if directory else {source.name:file_hash(source)}
    if not directory and source.stat().st_size>2*1024**3:
        raise ValueError("Archive intake exceeds 2 GiB")
    archive_names=[name for name in before if name.casefold().endswith(".rpf")]
    if not archive_names and directory and not force_copy:
        yield source,{"source_kind":"package_folder","source_sha256":digest(before),"source_inventory":before,"archives":[],"members":[]}
        if _inventory(source,limit=1000)!=before:
            raise ValueError("Package changed during inspection")
        return
    with tempfile.TemporaryDirectory(prefix="allin1-package-intake-") as temporary:
        root=Path(temporary)/"package";root.mkdir()
        source_bytes=sum(contained(source,name).stat().st_size for name in before) if directory else source.stat().st_size
        if shutil.disk_usage(root).free < source_bytes+64*1024**2:
            raise ValueError("Insufficient staging space for the package inspection copy")
        for name in before:
            target=contained(root,name);target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(contained(source,name) if directory else source,target)
        if _inventory(root,limit=1000)!=before:
            raise ValueError("Package changed while making the inspection copy")
        archives=[];members=[];logical_bytes=0
        game=path(str(gta_path)) if gta_path else None
        for number,name in enumerate(archive_names):
            archive=contained(root,name)
            record={"path":name,"sha256":before[name],"status":"unexpanded"}
            archives.append(record)
            produced=False
            try:
                if number>=32:
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
                if len(leaves)+len(members)+len(before)>1000 or sum(entry.size for entry in leaves)+logical_bytes>2*1024**3:
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
                    if logical_bytes>2*1024**3:
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
        _inventory(root,limit=1000)
        provenance={"source_kind":"package_folder" if directory else "rpf_archive","source_sha256":digest(before) if directory else before[source.name],
            "source_inventory":before,"archives":archives,"members":members}
        yield root,provenance
        after=_inventory(source,limit=1000) if directory else {source.name:file_hash(source)}
        if after!=before:
            raise ValueError("Original package/archive changed during inspection")
