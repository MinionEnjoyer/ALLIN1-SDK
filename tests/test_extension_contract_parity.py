"""Keep the SDK's declarative content contract aligned with the launcher."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from allin1_sdk.extensions import ExtensionManifest


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
