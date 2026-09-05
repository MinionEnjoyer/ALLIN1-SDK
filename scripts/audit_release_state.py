"""Read-only source/staged/installed SDK identity audit. Never starts an app."""
import argparse
import json
import stat
from pathlib import Path

from allin1_sdk.release_identity import sha256, source_identity, verify_inventory
from allin1_sdk.release_paths import contained, no_links, strict_json


def artifact(path):
    hard_links = 1
    try:
        no_links(path)
    except ValueError:
        # Cargo uses hard links for its executable output. Hashing this exact
        # named file is read-only; mutation paths still reject hard links.
        no_links(path.parent)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or info.st_nlink <= 1:
            raise
        hard_links = info.st_nlink
    return {"present": path.is_file(), "sha256": sha256(path) if path.is_file() else None,
            "bytes": path.stat().st_size if path.is_file() else None, "hard_link_count": hard_links}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-root", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    resources = root / "desktop/src-tauri/standalone-resources"
    staged = {"shell": root / "desktop/src-tauri/target/release/allin1-sdk-desktop.exe",
              "sidecar": root / "desktop/src-tauri/sidecar/ALLIN1-SDK-Desktop-Sidecar.exe",
              "rpf_helper": resources / "tools/RpfPatcher/RpfPatcher.dll"}
    result = {"schema_version": 1, "kind": "read_only_release_audit", "source": source_identity(root),
        "staged": {name: artifact(path) for name, path in staged.items()},
        "installers": {}, "package_integrity": "NOT TESTED", "automated_tests": "NOT TESTED", "live_acceptance": "NOT TESTED"}
    for path in sorted((root / "desktop/src-tauri/target/release/bundle/nsis").glob("*-setup.exe")):
        entry = artifact(path)
        companion = path.with_name(path.name + ".sha256")
        entry["companion_matches"] = companion.is_file() and companion.read_text().strip() == f"{entry['sha256']}  {path.name}"
        result["installers"][path.name] = entry
    try:
        manifest = verify_inventory(resources)
        result["staged_resource_integrity"] = {"status": "PASS", "file_count": len(manifest)}
    except (ValueError, OSError) as error:
        result["staged_resource_integrity"] = {"status": "FAIL", "error": str(error)}
    if args.installed_root:
        installed = no_links(args.installed_root).resolve(strict=True)
        result["installed"] = {name: artifact(installed / relative) for name, relative in {
            "shell": "allin1-sdk-desktop.exe", "sidecar": "sidecar/ALLIN1-SDK-Desktop-Sidecar.exe",
            "rpf_helper": "tools/RpfPatcher/RpfPatcher.dll"}.items()}
        result["installed_matches_staged"] = {
            name: result["installed"][name]["sha256"] == result["staged"][name]["sha256"]
            for name in staged}
        manifest_path = contained(installed, "resource-checksums.json")
        if manifest_path.is_file():
            declared = strict_json(manifest_path.read_bytes())
            mismatches = [name for name, digest in declared.items()
                if artifact(contained(installed, name))["sha256"] != digest]
            result["installed_declared_resources"] = {"checked": len(declared), "mismatches": mismatches}
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
