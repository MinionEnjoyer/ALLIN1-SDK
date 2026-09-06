"""Implementation fingerprints that work in source and frozen sidecar builds."""
from pathlib import Path
import sys

from allin1_sdk.artifact_contract import digest
from allin1_sdk.workspace_desktop import file_hash
from allin1_sdk.release_paths import no_links


def identify(files):
    files=[Path(file) for file in files]+[Path(__file__)]
    names=sorted({file.name for file in files})
    if getattr(sys,"frozen",False):
        value={"kind":"frozen_sidecar","modules":names,"executable_sha256":file_hash(no_links(Path(sys.executable))),
               "scope":"Exact executing sidecar bytes containing these Python modules; not loose .py files, a publisher signature or complete external-environment certification."}
    else:
        value={"kind":"python_sources","files":{file.name:file_hash(no_links(file)) for file in files},
               "scope":"Selected validation implementation sources; native helper and interpreter dependencies are separate evidence."}
    value["sha256"]=digest(value)
    return value
