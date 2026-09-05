"""Read-only, fail-closed live qualification against independently pinned evidence.

No game is launched. Neither trust pins nor acceptance evidence are generated.
"""
import argparse
import hashlib
import json
from pathlib import Path

from allin1_sdk.release_identity import require_reviewed_source
from allin1_sdk.release_paths import no_links, strict_json
from allin1_sdk.release_qualification import validate_live_acceptance


def pinned_json(path: Path, digest: str):
    path = no_links(path)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Pinned identity/session file exceeds size limit")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Independent identity/session trust pin does not match")
    return strict_json(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("identity", "session", "report", "evidence-root", "artifact-root", "dependency-root"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("identity-sha256", "session-sha256", "reviewed-commit", "version"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--edition", choices=("Legacy", "Enhanced"), required=True)
    parser.add_argument("--suite", choices=("sdk-desktop", "reactor-story"), required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = require_reviewed_source(root, args.reviewed_commit, args.version)
    identity = pinned_json(args.identity, args.identity_sha256)
    if (identity["sdk_commit"] != source["sdk_commit"] or identity["source_tree_sha256"] != source["source_tree_sha256"]
            or identity["sdk_version"] != args.version):
        raise ValueError("Pinned build identity is not the reviewed SDK source")
    report_path = no_links(args.report)
    if report_path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Acceptance report exceeds size limit")
    result = validate_live_acceptance(strict_json(report_path.read_bytes()), expected_identity=identity,
        trusted_session=pinned_json(args.session, args.session_sha256), evidence_root=args.evidence_root,
        artifact_root=args.artifact_root, dependency_root=args.dependency_root,
        target_edition=args.edition, suite=args.suite)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
