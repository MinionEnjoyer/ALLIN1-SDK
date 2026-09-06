"""Dependency-free SDK/Launcher provenance envelope. Hashes are not signatures."""
import hashlib
import json
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def seal(value, field):
    if field in value:
        raise ValueError("Identity already sealed")
    return {**value, field:digest(value)}


def sha(value):
    if not isinstance(value,str) or not re.fullmatch(r"[a-f0-9]{64}",value):
        raise ValueError("Expected a lowercase SHA-256 identity")
    return value


def verify_seal(value, field):
    if not isinstance(value,dict) or sha(value.get(field)) != digest({k:v for k,v in value.items() if k!=field}):
        raise ValueError(f"Content identity mismatch: {field}")


def inventory(value):
    if not isinstance(value,dict) or not value or len(value)>2000:
        raise ValueError("Expected a bounded nonempty artifact inventory")
    seen=set()
    for name, checksum in value.items():
        if not isinstance(name,str) or len(name)>1024:
            raise ValueError("Invalid artifact path")
        for part in name.split("/"):
            if (not part or part in {".",".."} or part!=part.strip() or part.endswith(".")
                or any(ord(c)<32 or c in '\\<>:"|?*' for c in part)
                or re.fullmatch(r"(?i)(con|prn|aux|nul|conin\$|conout\$|com[1-9¹²³]|lpt[1-9¹²³])",part.split(".")[0])):
                raise ValueError("Unsafe artifact path")
        folded=name.casefold()
        if folded in seen:
            raise ValueError("Ambiguous case-folded artifact paths")
        seen.add(folded)
        sha(checksum)
    for name in seen:
        parts=name.split("/")
        if any("/".join(parts[:index]) in seen for index in range(1,len(parts))):
            raise ValueError("Artifact file/directory conflict")


def validate_build(build):
    verify_seal(build,"build_fingerprint")
    if build.get("kind")!="sdk_execution_identity" or type(build.get("schema_version")) is not int or build["schema_version"]!=1:
        raise ValueError("Unsupported SDK execution identity")
    if build.get("mode") not in {"development_dirty","development_clean","frozen_verified_resources"}:
        raise ValueError("Unknown SDK execution provenance mode")
    if not isinstance(build.get("sdk_version"),str) or not build["sdk_version"]:
        raise ValueError("Missing SDK version")
    sha(build.get("executable_sha256"))
    inventory(build.get("resource_files"))
    if not isinstance(build.get("source"),dict):
        raise ValueError("Missing SDK source/build identity")
    return build


def validate_manifest(value):
    if not isinstance(value,dict) or set(value)!={"schema_version","kind","build","inputs","outputs","edition","validation_reports","changes_sha256","artifact_id"}:
        raise ValueError("Unsupported artifact manifest fields")
    if type(value["schema_version"]) is not int or value["schema_version"]!=1 or value["kind"]!="sdk_artifact_manifest":
        raise ValueError("Unsupported artifact manifest")
    verify_seal(value,"artifact_id")
    validate_build(value["build"])
    inventory(value["inputs"])
    inventory(value["outputs"])
    if value["edition"] not in {None,"Legacy","Enhanced"}:
        raise ValueError("Unknown artifact edition")
    if not isinstance(value["validation_reports"],list) or len(value["validation_reports"])>64:
        raise ValueError("Invalid validation-report identities")
    for item in value["validation_reports"]:
        sha(item)
    sha(value["changes_sha256"])
    return value
