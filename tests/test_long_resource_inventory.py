"""Exercise long Windows paths without changing host policy or touching GTA."""
import hashlib
import json

from allin1_sdk.release_identity import verify_inventory, verify_runtime_resources
from allin1_sdk.release_paths import filesystem_path, tree_files


def test_long_path_inventory_and_runtime_resource_agree(tmp_path):
    root = tmp_path
    while len(str(root)) < 285:
        root /= "long disposable resource directory"
    payload = filesystem_path(root / "docs/manual.md")
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"packaged documentation")
    manifest = {"docs/manual.md": hashlib.sha256(payload.read_bytes()).hexdigest()}
    encoded = json.dumps(manifest).encode()
    filesystem_path(root / "resource-checksums.json").write_bytes(encoded)
    trusted = tmp_path / "trusted.json"; trusted.write_bytes(encoded)
    assert set(tree_files(root)) == {"docs/manual.md", "resource-checksums.json"}
    assert verify_inventory(root) == manifest
    verify_runtime_resources(root, trusted)
