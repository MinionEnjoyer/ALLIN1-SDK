"""Shared Launcher / standalone SDK GBAY firearm contract tests."""
import json
from pathlib import Path

import pytest
from allin1_sdk.mods import ModManifest, open_mod_package
from allin1_sdk.weapon_catalog import WeaponCatalog, MAX_WEAPON_CATALOG_BYTES
from allin1_sdk.official_weapon_names import OFFICIAL_WEAPON_NAMES


def catalog():
    return {
        "schema_version": 1, "id": "test-weapons", "name": "Test Weapons",
        "weapons": [{
            "weapon": "WEAPON_A1_KRISS_VECTOR", "name": "KRISS Vector",
            "category": "smgs", "price": 7500, "ammo_cost_per_round": 2,
            "source_pack": "a1_krissvector",
        }],
    }


def package(tmp_path, payload=None, *, requires=True):
    (tmp_path / "dlc.rpf").write_bytes(b"RPF7 test")
    (tmp_path / "weapons.json").write_text(json.dumps(payload or catalog()), encoding="utf-8")
    (tmp_path / "allin1.content.json").write_text(json.dumps({
        "schema_version": 1, "api_version": 1, "id": "test.weapon",
        "name": "Test Weapon", "version": "1.0.0", "capabilities": ["gbay.catalogs"],
        "systems": [], "gbay": {"sections": [], "catalogs": [{
            "id": "test-weapons", "kind": "weapon",
            "source": "scripts/ALLIN1/Catalogs/test.weapon/weapons.json",
        }]}, "runtime": {"assemblies": []},
    }), encoding="utf-8")
    (tmp_path / "mod.toml").write_text('''schema_version = 2
id = "test.weapon"
name = "Test Weapon"
version = "1.0.0"
type = "mixed"
editions = ["enhanced"]
dependencies = ["openrpf"]
dlc_packs = ["a1_krissvector"]

[allin1]
api_version = 1
content = "allin1.content.json"
requires = REQUIRES

[[files]]
source = "dlc.rpf"
destination = "mods/update/x64/dlcpacks/a1_krissvector/dlc.rpf"

[[files]]
source = "weapons.json"
destination = "scripts/ALLIN1/Catalogs/test.weapon/weapons.json"
'''.replace("REQUIRES", '["allin1.online-content>=0.6.1"]' if requires else "[]"), encoding="utf-8")
    return tmp_path


def test_valid_separate_firearm():
    parsed = WeaponCatalog.from_dict(catalog())
    parsed.validate_package_ownership(["a1_krissvector"])
    assert parsed.weapons[0].price == 7500
    assert parsed.weapons[0].ammo_cost_per_round == 2


@pytest.mark.parametrize(("key", "value"), [
    ("price", True), ("price", -1), ("price", 1.5), ("price", 2_000_000_001),
    ("ammo_cost_per_round", False), ("ammo_cost_per_round", 1_000_001),
    ("weapon", "WEAPON_SMG\n"), ("weapon", "weapon_custom"), ("weapon", "WEAPON_"),
    ("name", "~r~Untrusted"), ("name", " x"), ("name", "x" * 129),
    ("category", "throwables"), ("source_pack", "../escape"), ("source_pack", "base"),
])
def test_invalid_entry(key, value):
    raw = catalog()
    raw["weapons"][0][key] = value
    with pytest.raises(ValueError):
        WeaponCatalog.from_dict(raw)


@pytest.mark.parametrize("version", [True, 1.0, 2, "1"])
def test_invalid_version(version):
    raw = catalog()
    raw["schema_version"] = version
    with pytest.raises(ValueError):
        WeaponCatalog.from_dict(raw)


def test_duplicate_unknown_empty_and_bounded_entries():
    for alter in (
        lambda raw: raw.update(unknown=True),
        lambda raw: raw.update(weapons=[]),
        lambda raw: raw["weapons"].append(raw["weapons"][0].copy()),
        lambda raw: raw.update(weapons=raw["weapons"] * 2049),
    ):
        raw = catalog()
        alter(raw)
        with pytest.raises(ValueError):
            WeaponCatalog.from_dict(raw)


def test_unowned_and_stock_rejected():
    with pytest.raises(ValueError, match="unowned"):
        WeaponCatalog.from_dict(catalog()).validate_package_ownership(["otherpack"])
    for name in ["WEAPON_SMG", "WEAPON_ALLIN1_SMOKE_RED"]:
        raw = catalog()
        raw["weapons"][0]["weapon"] = name
        with pytest.raises(ValueError, match="stock"):
            WeaponCatalog.from_dict(raw).validate_package_ownership(["a1_krissvector"])
    assert len(OFFICIAL_WEAPON_NAMES) >= 118


def test_json_duplicate_and_oversize_rejected(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text('{"id":"aa","id":"bb"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        WeaponCatalog.load(path)
    path.write_bytes(b" " * (MAX_WEAPON_CATALOG_BYTES + 1))
    with pytest.raises(ValueError, match="4 MiB"):
        WeaponCatalog.load(path)


def test_package_validates_catalog_and_opens_zip(tmp_path):
    import zipfile
    root = tmp_path / "package"
    root.mkdir()
    manifest = ModManifest.load(package(root))
    assert manifest.extension.gbay_catalogs[0].kind == "weapon"
    archive = tmp_path / "test.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for path in root.iterdir():
            zipped.write(path, path.name)
    with open_mod_package(archive) as result:
        assert result.mod_id == "test.weapon"


@pytest.mark.parametrize("bad", ["source_pack", "id", "price", "stock", "requires"])
def test_package_rejects_invalid_weapon_contract(tmp_path, bad):
    raw = catalog()
    if bad == "source_pack": raw["weapons"][0]["source_pack"] = "otherpack"
    if bad == "id": raw["id"] = "different-id"
    if bad == "price": raw["weapons"][0]["price"] = True
    if bad == "stock": raw["weapons"][0]["weapon"] = "WEAPON_SMG"
    with pytest.raises(ValueError):
        ModManifest.load(package(tmp_path, raw, requires=bad != "requires"))
