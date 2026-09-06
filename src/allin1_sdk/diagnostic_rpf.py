"""Reinspect exact receipted RPF members using private read-only container copies."""
from pathlib import Path
import shutil
import tempfile

from allin1_sdk.paths import project_root
from allin1_sdk.release_paths import contained
from allin1_sdk.rpf_tools import RpfExplorerService
from allin1_sdk.workspace_desktop import file_hash


def inspect(game, receipt, installed_artifact, selected_artifact):
    lineage = receipt["sdk_provenance"]
    members = lineage.get("rpf_members", [])
    records = receipt.get("rpf_entries", [])
    if not isinstance(members, list) or len(members)>512 or not isinstance(records, list) or len(records)>512:
        raise ValueError("Unbounded RPF installation receipt")
    rows=[];seen=set()
    for item in members:
        if not isinstance(item,dict): raise ValueError("Malformed RPF lineage")
        source, archive, entry = (item.get(key) for key in ("source","archive","entry"))
        if not isinstance(source,str) or source not in installed_artifact["outputs"] or not isinstance(archive,str) or not archive.startswith("mods/") or not isinstance(entry,str) or len(entry)>4096:
            raise ValueError("Unsupported RPF installation identity")
        contained(game,archive)
        segments=entry.split("!")
        if len(segments)>9:
            raise ValueError("RPF member nesting exceeds eight archives")
        for segment in segments: contained(game,segment)
        if any(not segment.lower().endswith(".rpf") for segment in segments[:-1]):
            raise ValueError("Invalid nested archive identity")
        key=(archive.casefold(),entry.casefold())
        if key in seen: raise ValueError("Duplicate installed RPF member")
        seen.add(key)
        recorded=installed_artifact["outputs"][source]
        matches=[row for row in records if isinstance(row,dict) and row.get("archive")==archive and row.get("entry")==entry]
        if item.get("sha256")!=recorded or len(matches)!=1 or matches[0].get("sha256")!=recorded:
            raise ValueError("RPF receipt source/member hash mapping is inconsistent")
        rows.append({"source":source,"archive":archive,"entry":entry,"receipted_sha256":recorded,
                     "expected_sha256":selected_artifact["outputs"].get(source),"actual_sha256":None,"status":"not_checked"})
    edition=installed_artifact["edition"]
    if not receipt["enabled"] or edition not in {"Legacy","Enhanced"}:
        for row in rows: row["reason"]="Package is disabled; active member bytes are not attributed to it." if not receipt["enabled"] else "Receipt has no explicit decoder edition."
        return rows
    archive_budget=2*1024**3;payload_budget=512*1024**2
    with tempfile.TemporaryDirectory(prefix="allin1-installed-rpf-check-") as temporary:
        root=Path(temporary)
        for ordinal,archive in enumerate(dict.fromkeys(row["archive"] for row in rows)):
            group=[row for row in rows if row["archive"]==archive]
            target=contained(game,archive)
            if not target.is_file():
                for row in group: row.update(status="missing_archive")
                continue
            if ordinal>=16 or target.stat().st_size>archive_budget:
                for row in group: row["reason"]="Archive exceeds the 16-container / 2-GiB read budget."
                continue
            size=target.stat().st_size
            archive_budget-=size
            if shutil.disk_usage(root).free<size+64*1024**2:
                for row in group: row["reason"]="Insufficient space for private archive verification."
                continue
            copy=root/f"container-{ordinal}.rpf"
            try:
                original_sha=file_hash(target)
                shutil.copyfile(target,copy)
                if file_hash(copy)!=original_sha:
                    raise ValueError("Container changed during private-copy creation")
                service=RpfExplorerService(project_root(),game)
                index=service.index(copy)
                if index.edition.casefold()!=edition.casefold() or index.warnings or len(index.entries)>25000:
                    raise ValueError("Decoder edition, incomplete index or index limit prevents exact verification")
                for number,row in enumerate(group):
                    row["container_sha256"]=original_sha
                    segments=row["entry"].split("!")
                    archive_path="!".join(segments[:-1]);entry_path=segments[-1]
                    matches=[item for item in index.entries if item.archive_path.casefold()==archive_path.casefold() and item.path.casefold()==entry_path.casefold() and item.kind not in {"directory","archive"}]
                    if not matches:
                        row["status"]="missing_member";continue
                    if len(matches)!=1:
                        row["reason"]="Member identity is ambiguous in the current container.";continue
                    item=matches[0]
                    if item.size>min(payload_budget,128*1024**2) or shutil.disk_usage(root).free<item.size+64*1024**2:
                        row["reason"]="Member exceeds the extraction size or free-space budget.";continue
                    extracted=root/f"member-{ordinal}-{number}.bin"
                    try:
                        service.extract(index,item,extracted)
                        if extracted.stat().st_size>min(payload_budget,128*1024**2):
                            raise ValueError("Extracted member exceeded the declared read budget")
                        payload_budget-=extracted.stat().st_size
                        actual=row["actual_sha256"]=file_hash(extracted)
                        row["status"]="match" if actual==row["expected_sha256"] else "mismatch" if row["expected_sha256"] else "not_in_selected_artifact"
                        row["receipted_bytes_match"]=actual==row["receipted_sha256"]
                    finally:
                        extracted.unlink(missing_ok=True)
                if file_hash(target)!=original_sha:
                    raise ValueError("Installed container changed during verification")
            except (OSError,RuntimeError,ValueError):
                # Native errors may contain private paths. Do not export them.
                for row in group:
                    row.update(status="not_checked",actual_sha256=None,reason="Archive/member verification could not complete consistently; check decoder availability, edition and source stability.")
                    row.pop("receipted_bytes_match",None)
            finally:
                copy.unlink(missing_ok=True)
    return rows
