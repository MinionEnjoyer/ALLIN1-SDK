"""Focused coverage for the declarative extension contract.

These tests exercise validation and persistence boundaries that are shared by
the SDK and launcher without invoking executable extension content.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from allin1_sdk.extensions import (
    ExtensionCatalog,
    ExtensionManifest,
    ExtensionSetting,
    ExtensionSettingsStore,
    apply_settings_to_config,
    settings_from_config,
)


def _manifest_payload(*, extension_id: str = "fixture.content") -> dict[str, object]:
    return {
        "schema_version": 1,
        "api_version": 1,
        "id": extension_id,
        "name": "Fixture Content",
        "version": "1.2.3",
        "description": "Declarative contract fixture.",
        "capabilities": [
            "launcher.settings",
            "gbay.sections",
            "gbay.catalogs",
        ],
        "systems": [{
            "id": "fixture-system",
            "name": "Fixture System",
            "description": "Exercises typed settings.",
            "category": "Testing",
            "experimental": False,
            "enabled_by_default": True,
            "settings": [
                {
                    "key": "enabled",
                    "label": "Enabled",
                    "type": "boolean",
                    "default": True,
                    "config_key": "general.enabled",
                },
                {
                    "key": "count",
                    "label": "Count",
                    "type": "integer",
                    "default": 2,
                    "minimum": 1,
                    "maximum": 5,
                    "step": 1,
                },
                {
                    "key": "gain",
                    "label": "Gain",
                    "type": "number",
                    "default": 0.5,
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "step": 0.1,
                },
                {
                    "key": "label",
                    "label": "Label",
                    "type": "string",
                    "default": "fixture",
                },
                {
                    "key": "mode",
                    "label": "Mode",
                    "type": "choice",
                    "default": "safe",
                    "choices": ["safe", "fast", "safe"],
                },
            ],
        }],
        "gbay": {
            "sections": [{
                "id": "fixture-section",
                "label": "Fixture",
                "description": "Fixture listings.",
                "route": "fixture:listings",
                "order": 25,
            }],
            "catalogs": [{
                "id": "fixture-catalog",
                "kind": "vehicle",
                "source": "scripts/Fixture/catalog.json",
            }],
        },
        "runtime": {
            "assemblies": [{
                "path": "scripts/Fixture/Fixture.Runtime.dll",
                "entry_point": "Fixture.Runtime.EntryPoint",
            }],
        },
    }


def test_complete_extension_contract_round_trips_settings_and_owned_files(
    tmp_path: Path,
) -> None:
    manifest = ExtensionManifest.from_dict(_manifest_payload())
    assert manifest.setting(" MODE ").choices == ("safe", "fast")
    assert ExtensionManifest.from_registry_entry(manifest.to_dict()).to_dict() == (
        manifest.to_dict()
    )
    manifest.validate_package_destinations([
        r"scripts\Fixture\Fixture.Runtime.dll",
        "scripts/Fixture/catalog.json",
    ])
    with pytest.raises(ValueError, match="Runtime assembly is not owned"):
        manifest.validate_package_destinations(["scripts/Fixture/catalog.json"])
    with pytest.raises(ValueError, match="GBAY catalog is not owned"):
        manifest.validate_package_destinations([
            "scripts/Fixture/Fixture.Runtime.dll",
        ])

    config = SimpleNamespace(general=SimpleNamespace(enabled=False))
    assert settings_from_config(manifest, config) == {"enabled": False}
    apply_settings_to_config(manifest, config, {"enabled": True, "count": 3})
    assert config.general.enabled is True

    store = ExtensionSettingsStore(tmp_path / "state" / "settings.json")
    assert store.effective(manifest)["count"] == 2
    effective = store.update(manifest, {"count": 4, "mode": "fast"})
    assert effective["count"] == 4
    assert effective["mode"] == "fast"
    stored = json.loads(store.path.read_text(encoding="utf-8"))
    stored["extensions"][manifest.extension_id]["count"] = 99
    store.path.write_text(json.dumps(stored), encoding="utf-8")
    assert store.effective(manifest)["count"] == 2
    store.remove(manifest.extension_id)
    assert store.effective(manifest)["mode"] == "safe"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"key": "bad key", "label": "Bad", "type": "string", "default": "x"}, "safe lowercase"),
        ({"key": "x", "label": "Bad", "type": "opaque", "default": "x"}, "must be one of"),
        ({"key": "x", "label": "Bad", "type": "choice", "default": "x"}, "choices is required"),
        ({"key": "x", "label": "Bad", "type": "string", "default": "x", "choices": ["x"]}, "valid only"),
        ({"key": "x", "label": "Bad", "type": "number", "default": 1, "minimum": True}, "finite number"),
        ({"key": "x", "label": "Bad", "type": "integer", "default": 1, "minimum": 2, "maximum": 1}, "must not exceed"),
        ({"key": "x", "label": "Bad", "type": "integer", "default": 1, "step": 0}, "must be positive"),
        ({"key": "x", "label": "Bad", "type": "string", "default": "x", "minimum": 0}, "numeric bounds"),
        ({"key": "x", "label": "Bad", "type": "boolean", "default": True, "config_key": "unsafe.value"}, "not a supported"),
    ],
)
def test_extension_setting_rejects_invalid_contracts(
    payload: dict[str, object], message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        ExtensionSetting.from_dict(payload, "setting")


def test_extension_catalog_discovers_sorted_manifests_and_rejects_duplicates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "content"
    for name, extension_id in (("z-last", "fixture.zed"), ("a-first", "fixture.alpha")):
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "allin1.content.json").write_text(
            json.dumps(_manifest_payload(extension_id=extension_id)), encoding="utf-8",
        )
    assert [item.extension_id for item in ExtensionCatalog(root).discover()] == [
        "fixture.alpha", "fixture.zed",
    ]
    duplicate = root / "duplicate.content.json"
    duplicate.write_text(
        json.dumps(_manifest_payload(extension_id="fixture.alpha")), encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate package ids"):
        ExtensionCatalog(root).discover()
