"""Keep the SDK's declarative content contract aligned with the launcher."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from allin1_sdk.extensions import ExtensionManifest, ExtensionRegistry


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER_ROOT = ROOT.parent / "ALLIN1"


def _normalized_contract_source(path: Path, namespace: str) -> str:
    return (
        path.read_text(encoding="utf-8")
        .replace(
            f"from {namespace}.mod_package_contract import (",
            "from CONTRACT.mod_package_contract import (",
        )
        .rstrip()
    )


def test_extension_contract_implementation_matches_launcher_copy() -> None:
    launcher = LAUNCHER_ROOT / "src" / "allin1" / "extensions.py"
    if not launcher.is_file():
        pytest.skip("Sibling ALLIN1 launcher checkout is not present")
    sdk = ROOT / "src" / "allin1_sdk" / "extensions.py"
    assert _normalized_contract_source(sdk, "allin1_sdk") == (
        _normalized_contract_source(launcher, "allin1")
    )


def _tree_snapshot(root: Path) -> dict[str, tuple[int, bytes] | None]:
    return {
        path.relative_to(root).as_posix(): (
            (path.stat().st_mtime_ns, path.read_bytes()) if path.is_file() else None
        )
        for path in root.rglob("*")
    }


def test_empty_registry_inspection_creates_no_state(tmp_path: Path) -> None:
    game = tmp_path / "game with spaces"
    game.mkdir()
    (game / "user-data.txt").write_bytes(b"preserve user data")
    before = _tree_snapshot(tmp_path)
    assert ExtensionRegistry(game).inspect()["extensions"] == []
    assert _tree_snapshot(tmp_path) == before


def test_installed_manifest_inspection_is_pure_and_matches_rebuild(tmp_path: Path) -> None:
    manifest = ExtensionManifest.from_dict({
        "schema_version": 1, "api_version": 1, "id": "fixture.inspection",
        "name": "Inspection fixture", "version": "1.0.0",
        "description": "Read-only registry fixture", "capabilities": [],
        "systems": [{"id": "inspection", "name": "Inspection", "settings": []}],
        "gbay": {"sections": [], "catalogs": []},
        "runtime": {"assemblies": []},
    })
    registry = ExtensionRegistry(tmp_path / "game with spaces")
    registry.register_builtin(manifest)
    before = _tree_snapshot(tmp_path)
    viewed = registry.inspect()
    assert registry.installed_manifest(manifest.extension_id).to_dict() == manifest.to_dict()
    assert _tree_snapshot(tmp_path) == before
    assert viewed["extensions"] == registry.rebuild()["extensions"]


@pytest.mark.parametrize("identifier, error", [("missing-package", KeyError), ("../escape", ValueError)])
def test_failed_manifest_inspection_does_not_write(tmp_path: Path, identifier: str, error: type[Exception]) -> None:
    registry = ExtensionRegistry(tmp_path / "game")
    before = _tree_snapshot(tmp_path)
    with pytest.raises(error):
        registry.installed_manifest(identifier)
    assert _tree_snapshot(tmp_path) == before


def test_builtin_world_maps_authorize_multiple_hashed_descriptors(
    tmp_path: Path,
) -> None:
    game = tmp_path / "game"
    game.mkdir()
    manifest = ExtensionManifest.from_dict({
        "schema_version": 1,
        "api_version": 1,
        "id": "allin1.map-fixture",
        "name": "ALLIN1 Map Fixture",
        "version": "1.0.0",
        "description": "Built-in map receipt fixture.",
        "capabilities": ["world.maps"],
        "systems": [{
            "id": "map-system",
            "name": "Map System",
            "settings": [],
        }],
        "gbay": {"sections": [], "catalogs": []},
        "runtime": {"assemblies": []},
    })
    destination_root = (
        game / "scripts" / "ALLIN1" / "Maps" / manifest.extension_id
    )
    destination_root.mkdir(parents=True)
    records = []
    for name in ("city.maps.json", "country.maps.json"):
        content = (name + "\n").encode("utf-8")
        destination = destination_root / name
        destination.write_bytes(content)
        records.append({
            "path": destination.relative_to(game).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
        })

    registry = ExtensionRegistry(game)
    registry.register_builtin(manifest, map_files=records)
    entry = registry.installed()[0]
    assert entry["enabled"] is True
    assert entry["map_files"] == records

    (destination_root / "country.maps.json").write_bytes(b"tampered")
    before = _tree_snapshot(tmp_path)
    blocked = registry.inspect()["extensions"][0]
    assert blocked["enabled"] is False
    assert "failed its built-in hash" in blocked["blocked_reason"]
    assert _tree_snapshot(tmp_path) == before
    assert registry.installed()[0] == blocked


def test_full_contract_rejects_duplicate_system_ids(tmp_path: Path) -> None:
    descriptor = {
        "schema_version": 1,
        "api_version": 1,
        "id": "fixture.duplicate",
        "name": "Duplicate fixture",
        "version": "1.0.0",
        "description": "Contract regression fixture.",
        "capabilities": ["launcher.settings"],
        "systems": [
            {"id": "same-system", "name": "First", "settings": []},
            {"id": "same-system", "name": "Second", "settings": []},
        ],
        "gbay": {"sections": [], "catalogs": []},
        "runtime": {"assemblies": []},
    }
    path = tmp_path / "allin1.content.json"
    path.write_text(json.dumps(descriptor), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate system ids"):
        ExtensionManifest.load(path)
