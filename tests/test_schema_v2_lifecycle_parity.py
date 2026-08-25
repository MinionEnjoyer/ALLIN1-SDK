"""Regression coverage for launcher-compatible schema-2 package lifecycles.

These tests deliberately exercise the SDK service through the receipt boundary
consumed by the launcher.  A schema-2 package is not lifecycle-compatible when
the manifest validates but its requirements, embedded extension descriptor, or
registry visibility are discarded during installation.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from allin1_sdk.mods import ModIntegrationService, ModManifest


LAUNCHER_ROOT = Path(__file__).resolve().parents[2] / "ALLIN1"
SUPPRESSOR_PACKAGE = LAUNCHER_ROOT / "mods" / "realistic-suppressors"


def _game(root: Path) -> Path:
    game = root / "GTA V Enhanced"
    game.mkdir(parents=True)
    (game / "GTA5_Enhanced.exe").write_bytes(b"MZ")
    return game


def _schema_v2_package(
    root: Path,
    package_id: str,
    *,
    version: str = "1.0.0",
    requires: tuple[str, ...] = (),
) -> Path:
    package = root / package_id
    package.mkdir(parents=True)
    marker = package / "marker.json"
    marker.write_text(json.dumps({"owner": package_id}), encoding="utf-8")
    descriptor = {
        "schema_version": 1,
        "api_version": 1,
        "id": package_id,
        "name": f"Fixture {package_id}",
        "version": version,
        "description": "Schema-2 lifecycle regression fixture.",
        "capabilities": ["launcher.settings"],
        "systems": [{
            "id": "fixture-system",
            "name": "Fixture System",
            "category": "Diagnostics",
            "settings": [{
                "key": "enabled",
                "label": "Enabled",
                "type": "boolean",
                "default": True,
            }],
        }],
        "gbay": {"sections": [], "catalogs": []},
        "runtime": {"assemblies": []},
    }
    descriptor_path = package / "allin1.content.json"
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    requirement_text = ", ".join(json.dumps(value) for value in requires)
    (package / "mod.toml").write_text(
        "schema_version = 2\n"
        f"id = {json.dumps(package_id)}\n"
        f"name = {json.dumps('Fixture ' + package_id)}\n"
        f"version = {json.dumps(version)}\n"
        'type = "config"\n'
        'editions = ["enhanced"]\n'
        "dependencies = []\n"
        "conflicts = []\n\n"
        "[allin1]\n"
        "api_version = 1\n"
        'content = "allin1.content.json"\n'
        f"requires = [{requirement_text}]\n\n"
        "[[files]]\n"
        'source = "allin1.content.json"\n'
        f"destination = {json.dumps(f'scripts/{package_id}/allin1.content.json')}\n\n"
        "[[files]]\n"
        'source = "marker.json"\n'
        f"destination = {json.dumps(f'scripts/{package_id}/marker.json')}\n",
        encoding="utf-8",
    )
    return package


def _loose_package(
    root: Path,
    package_id: str,
    *,
    destination: str = "scripts/fixture.ini",
    payload: bytes = b"managed",
) -> Path:
    package = root / package_id
    package.mkdir(parents=True)
    (package / "payload.bin").write_bytes(payload)
    (package / "mod.toml").write_text(
        "schema_version = 1\n"
        f"id = {json.dumps(package_id)}\n"
        f"name = {json.dumps('Fixture ' + package_id)}\n"
        'version = "1.0.0"\n'
        'type = "config"\n'
        'editions = ["enhanced"]\n'
        "dependencies = []\n"
        "conflicts = []\n\n"
        "[[files]]\n"
        'source = "payload.bin"\n'
        f"destination = {json.dumps(destination)}\n",
        encoding="utf-8",
    )
    return package


def _launcher_registry(game: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    launcher_source = LAUNCHER_ROOT / "src"
    if not launcher_source.is_dir():
        pytest.skip("Sibling ALLIN1 launcher checkout is not present")
    monkeypatch.syspath_prepend(str(launcher_source))
    # Keep this import local so the SDK remains the implementation under test;
    # the launcher is used only as the authoritative receipt consumer.
    from allin1.extensions import ExtensionRegistry

    return ExtensionRegistry(game).rebuild()


def test_missing_schema_v2_requirement_is_refused_before_payload_write(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path)
    package = _schema_v2_package(
        tmp_path / "packages",
        "fixture.dependent",
        requires=("fixture.foundation>=1.2.0",),
    )
    service = ModIntegrationService(game)
    error: ValueError | None = None
    try:
        service.install(ModManifest.load(package))
    except ValueError as exc:
        error = exc

    target = game / "scripts" / "fixture.dependent" / "marker.json"
    receipt = game / "scripts" / ".allin1" / "mods" / "fixture.dependent.json"
    assert not target.exists(), "requirements must be checked before payload copy"
    assert not receipt.exists(), "a refused install must not leave a receipt"
    assert error is not None
    assert "Missing required ALLIN1 content package" in str(error)


def test_satisfied_schema_v2_requirement_preserves_receipt_and_registry_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    game = _game(tmp_path)
    foundation = _schema_v2_package(
        tmp_path / "foundation", "fixture.foundation", version="1.2.0",
    )
    dependent = _schema_v2_package(
        tmp_path / "dependent",
        "fixture.dependent",
        requires=("fixture.foundation>=1.2.0",),
    )
    service = ModIntegrationService(game)
    service.install(ModManifest.load(foundation))
    service.install(ModManifest.load(dependent))

    receipt = service.inspect_receipt("fixture.dependent")
    observed = {
        "schema_version": receipt.get("schema_version"),
        "requires": receipt.get("requires"),
        "extension_id": (receipt.get("extension") or {}).get("id"),
        "extension_version": (receipt.get("extension") or {}).get("version"),
    }
    assert observed == {
        "schema_version": 2,
        "requires": ["fixture.foundation>=1.2.0"],
        "extension_id": "fixture.dependent",
        "extension_version": "1.0.0",
    }

    registry = _launcher_registry(game, monkeypatch)
    entries = {item["id"]: item for item in registry["extensions"]}
    assert set(entries) == {"fixture.foundation", "fixture.dependent"}
    assert entries["fixture.dependent"]["enabled"] is True
    assert entries["fixture.dependent"]["requires"] == [
        "fixture.foundation>=1.2.0"
    ]


def test_schema_v2_dependency_and_owned_file_guards_fail_before_mutation(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path)
    foundation = _schema_v2_package(
        tmp_path / "foundation", "fixture.foundation", version="1.2.0",
    )
    dependent = _schema_v2_package(
        tmp_path / "dependent", "fixture.dependent",
        requires=("fixture.foundation>=1.2.0",),
    )
    service = ModIntegrationService(game)
    service.install(ModManifest.load(foundation))
    service.install(ModManifest.load(dependent))

    with pytest.raises(ValueError, match="is required by"):
        service.set_enabled("fixture.foundation", False)
    with pytest.raises(ValueError, match="is required by"):
        service.uninstall("fixture.foundation")
    assert service.inspect_receipt("fixture.foundation")["enabled"] is True

    managed = game / "scripts" / "fixture.dependent" / "marker.json"
    managed.write_text("externally changed", encoding="utf-8")
    with pytest.raises(RuntimeError, match="externally changed"):
        service.set_enabled("fixture.dependent", False)
    with pytest.raises(RuntimeError, match="externally changed"):
        service.uninstall("fixture.dependent")
    assert managed.read_text(encoding="utf-8") == "externally changed"
    assert service.inspect_receipt("fixture.dependent")["enabled"] is True


def test_disabled_uninstall_refuses_unrelated_live_destination(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path)
    package = _loose_package(tmp_path / "packages", "fixture.unrelated")
    service = ModIntegrationService(game)
    service.install(ModManifest.load(package))
    service.set_enabled("fixture.unrelated", False)

    target = game / "scripts" / "fixture.ini"
    disabled = target.with_name(target.name + ".disabled")
    target.write_bytes(b"unrelated live file")

    with pytest.raises(RuntimeError, match="Unmanaged file appeared"):
        service.uninstall("fixture.unrelated")

    assert target.read_bytes() == b"unrelated live file"
    assert disabled.read_bytes() == b"managed"
    assert service.inspect_receipt("fixture.unrelated")["enabled"] is False


def test_disabled_uninstall_validates_and_restores_underlying_backup(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path)
    target = game / "scripts" / "fixture.ini"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original")
    package = _loose_package(tmp_path / "packages", "fixture.layered")
    service = ModIntegrationService(game)
    service.install(ModManifest.load(package))
    service.set_enabled("fixture.layered", False)

    # The uninstall path must restore a verified backup when the disabled
    # lifecycle has not already materialized that underlying layer.
    assert not target.exists()
    service.uninstall("fixture.layered")
    assert target.read_bytes() == b"original"
    assert not target.with_name(target.name + ".disabled").exists()
    assert not (service.state_root / "fixture.layered.json").exists()


def test_disabled_uninstall_refuses_changed_underlying_backup_layer(
    tmp_path: Path,
) -> None:
    game = _game(tmp_path)
    target = game / "scripts" / "fixture.ini"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original")
    package = _loose_package(tmp_path / "packages", "fixture.changed-layer")
    service = ModIntegrationService(game)
    service.install(ModManifest.load(package))
    service.set_enabled("fixture.changed-layer", False)

    target.write_bytes(b"changed underlying file")
    with pytest.raises(RuntimeError, match="Underlying file changed"):
        service.uninstall("fixture.changed-layer")

    assert target.read_bytes() == b"changed underlying file"
    assert target.with_name(target.name + ".disabled").read_bytes() == b"managed"
    assert service.inspect_receipt("fixture.changed-layer")["enabled"] is False


def test_uninstall_rolls_back_payload_and_receipt_when_registry_rebuild_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    game = _game(tmp_path)
    package = _loose_package(tmp_path / "packages", "fixture.registry-rollback")
    service = ModIntegrationService(game)
    service.install(ModManifest.load(package))

    from allin1_sdk.extensions import ExtensionRegistry

    real_rebuild = ExtensionRegistry.rebuild
    calls = 0

    def fail_once(registry: ExtensionRegistry):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("synthetic registry failure")
        return real_rebuild(registry)

    monkeypatch.setattr(ExtensionRegistry, "rebuild", fail_once)
    with pytest.raises(OSError, match="synthetic registry failure"):
        service.uninstall("fixture.registry-rollback")

    target = game / "scripts" / "fixture.ini"
    assert target.read_bytes() == b"managed"
    assert service.inspect_receipt("fixture.registry-rollback")["enabled"] is True


def test_uninstall_rolls_back_when_receipt_removal_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    game = _game(tmp_path)
    package = _loose_package(tmp_path / "packages", "fixture.receipt-rollback")
    service = ModIntegrationService(game)
    service.install(ModManifest.load(package))
    service.set_enabled("fixture.receipt-rollback", False)
    receipt_path = service.state_root / "fixture.receipt-rollback.json"
    real_unlink = Path.unlink

    def fail_receipt_unlink(path: Path, *args, **kwargs):
        if path == receipt_path:
            raise OSError("synthetic receipt removal failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_receipt_unlink)
    with pytest.raises(OSError, match="synthetic receipt removal failure"):
        service.uninstall("fixture.receipt-rollback")

    target = game / "scripts" / "fixture.ini"
    assert not target.exists()
    assert target.with_name(target.name + ".disabled").read_bytes() == b"managed"
    assert receipt_path.is_file()


@pytest.mark.skipif(
    not (SUPPRESSOR_PACKAGE / "mod.toml").is_file(),
    reason="Sibling Suppressors Enhanced fixture is not present",
)
def test_suppressor_schema_v2_install_remains_visible_to_launcher_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = ModManifest.load(SUPPRESSOR_PACKAGE)
    assert manifest.schema_version == 2
    assert manifest.extension is not None

    game = _game(tmp_path)
    service = ModIntegrationService(game)
    monkeypatch.setattr(service, "_check_dependencies", lambda _manifest: None)
    monkeypatch.setattr(
        service, "_set_dlc_registration", lambda _pack, _enabled: True,
    )
    service.install(manifest)

    receipt = service.inspect_receipt("realistic-suppressors")
    assert {
        "schema_version": receipt.get("schema_version"),
        "requires": receipt.get("requires"),
        "extension_id": (receipt.get("extension") or {}).get("id"),
    } == {
        "schema_version": 2,
        "requires": [],
        "extension_id": "realistic-suppressors",
    }
    registry = _launcher_registry(game, monkeypatch)
    suppressor = next(
        item for item in registry["extensions"]
        if item["id"] == "realistic-suppressors"
    )
    assert suppressor["enabled"] is True
    assert suppressor["runtime_files"][0]["path"] == (
        "scripts/RealisticSuppressors/RealisticSuppressors.dll"
    )
