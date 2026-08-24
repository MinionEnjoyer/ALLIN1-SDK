from __future__ import annotations

import stat
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

import allin1_sdk.mods as mods
from allin1_sdk.cli import main
from allin1_sdk.mods import ModManifest, open_mod_package


FIXTURES = Path(__file__).parent / "contract_fixtures" / "mod_packages"
LAUNCHER_FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "ALLIN1" / "tests" / "contract_fixtures" / "mod_packages"
)


@pytest.mark.parametrize(("folder", "schema"), [("schema_v1", 1), ("schema_v2", 2)])
def test_shared_schema_contract_fixtures(folder: str, schema: int) -> None:
    package = ModManifest.load(FIXTURES / folder)
    assert package.schema_version == schema
    assert (package.extension is not None) is (schema == 2)


def test_contract_fixture_bytes_match_launcher_copy() -> None:
    if not LAUNCHER_FIXTURES.is_dir():
        pytest.skip("Sibling ALLIN1 checkout is not present")
    local = {
        path.relative_to(FIXTURES): path.read_bytes()
        for path in FIXTURES.rglob("*") if path.is_file()
    }
    launcher = {
        path.relative_to(LAUNCHER_FIXTURES): path.read_bytes()
        for path in LAUNCHER_FIXTURES.rglob("*") if path.is_file()
    }
    assert local == launcher


def test_shared_contract_implementation_matches_launcher_copy() -> None:
    launcher_module = (
        Path(__file__).resolve().parents[2] / "ALLIN1" / "src" / "allin1"
        / "mod_package_contract.py"
    )
    if not launcher_module.is_file():
        pytest.skip("Sibling ALLIN1 checkout is not present")
    sdk_module = (
        Path(__file__).resolve().parents[1] / "src" / "allin1_sdk"
        / "mod_package_contract.py"
    )
    assert sdk_module.read_bytes() == launcher_module.read_bytes()


def _zip_tree(archive: Path, root: Path, prefix: str = "") -> None:
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        for source in root.rglob("*"):
            if source.is_file():
                package.write(source, prefix + source.relative_to(root).as_posix())


@pytest.mark.parametrize("prefix", ["", "source-repo/package/"])
def test_zip_import_accepts_one_root_or_nested_manifest(
    tmp_path: Path, prefix: str,
) -> None:
    archive = tmp_path / "package.zip"
    _zip_tree(archive, FIXTURES / "schema_v2", prefix)
    with open_mod_package(archive) as package:
        staged_root = package.package_root
        assert package.mod_id == "contract.schema-v2"
        assert package.schema_version == 2
        assert staged_root.is_dir()
    assert not staged_root.exists()


@pytest.mark.parametrize("entries", [[], ["one/mod.toml", "two/mod.toml"]])
def test_zip_import_rejects_zero_or_multiple_manifests(
    tmp_path: Path, entries: list[str],
) -> None:
    archive = tmp_path / "ambiguous.zip"
    with zipfile.ZipFile(archive, "w") as package:
        for entry in entries:
            package.writestr(entry, "schema_version = 1")
    with pytest.raises(ValueError, match="does not contain|multiple mod.toml"):
        with open_mod_package(archive):
            pass


def test_zip_import_rejects_traversal_and_symlinks(tmp_path: Path) -> None:
    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as package:
        package.writestr("package/mod.toml", "schema_version = 1")
        package.writestr("../escape.txt", "escape")
    with pytest.raises(ValueError, match="traversal"):
        with open_mod_package(traversal):
            pass

    linked = tmp_path / "linked.zip"
    link = zipfile.ZipInfo("package/payload/link.dll")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(linked, "w") as package:
        package.writestr("package/mod.toml", "schema_version = 1")
        package.writestr(link, "target")
    with pytest.raises(ValueError, match="links or special files"):
        with open_mod_package(linked):
            pass


def test_zip_import_enforces_expansion_limit(tmp_path: Path, monkeypatch) -> None:
    archive = tmp_path / "large.zip"
    _zip_tree(archive, FIXTURES / "schema_v1")
    monkeypatch.setattr(mods, "MAX_PACKAGE_ARCHIVE_BYTES", 8)
    with pytest.raises(ValueError, match="size limit"):
        with open_mod_package(archive):
            pass


def test_sdk_cli_validates_zip_package(tmp_path: Path) -> None:
    archive = tmp_path / "package.zip"
    _zip_tree(archive, FIXTURES / "schema_v2", "source-repo/package/")
    result = CliRunner().invoke(main, ["validate-package", str(archive)])
    assert result.exit_code == 0, result.output
    assert '"schema_version": 2' in result.output
