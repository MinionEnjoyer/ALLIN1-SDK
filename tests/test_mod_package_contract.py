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


def _mixed_destination_package(
    root: Path, destination: str,
) -> Path:
    package = root / "mixed-destination"
    package.mkdir(parents=True)
    (package / "payload.json").write_text("{}", encoding="utf-8")
    (package / "mod.toml").write_text(
        "schema_version = 1\n"
        'id = "vehicle-workbench-runtime"\n'
        'name = "Vehicle Workbench Runtime"\n'
        'version = "1.0.0"\n'
        'type = "mixed"\n'
        'editions = ["enhanced"]\n'
        'dependencies = []\n'
        'conflicts = []\n'
        '[[files]]\n'
        'source = "payload.json"\n'
        f'destination = "{destination}"\n',
        encoding="utf-8",
    )
    return package


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


@pytest.mark.parametrize(
    "destination",
    [
        "VehicleWorkbenchAxles/runtime.json",
        "VehicleWorkbenchAxles/profiles/compatibility.json",
    ],
)
def test_sdk_accepts_owned_vehicle_workbench_json_runtime_tree(
    tmp_path: Path, destination: str,
) -> None:
    package = _mixed_destination_package(tmp_path, destination)

    manifest = ModManifest.load(package)

    assert manifest.files[0].destination.as_posix() == destination


def test_sdk_accepts_isolated_plugin_runtime_tree(tmp_path: Path) -> None:
    package = _mixed_destination_package(
        tmp_path, "plugins/ReactorV/Renderer.dll",
    )

    manifest = ModManifest.load(package)

    assert manifest.files[0].destination.as_posix() == (
        "plugins/ReactorV/Renderer.dll"
    )


@pytest.mark.parametrize(
    ("destination", "message"),
    [
        ("OtherRuntime/runtime.json", "Mixed package files"),
        ("VehicleWorkbenchAxles.json", "Mixed package files"),
        ("VehicleWorkbenchAxles/runtime.dll", "Mixed package files"),
        ("VehicleWorkbenchAxles/tools/settings.exe", "Mixed package files"),
        ("VehicleWorkbenchAxles/../escape.json", "traversal"),
        ("VehicleWorkbenchAxles/runtime.json:alternate", "Windows-invalid"),
    ],
)
def test_sdk_vehicle_workbench_runtime_tree_exception_stays_narrow(
    tmp_path: Path, destination: str, message: str,
) -> None:
    package = _mixed_destination_package(tmp_path, destination)

    with pytest.raises(ValueError, match=message):
        ModManifest.load(package)


def test_sdk_mixed_runtime_tree_cannot_target_launcher_reserved_root(
    tmp_path: Path,
) -> None:
    package = _mixed_destination_package(tmp_path, "ScriptHookV.dll")

    with pytest.raises(ValueError, match="reserved by the ALLIN1 launcher"):
        ModManifest.load(package)


def test_sdk_mixed_runtime_tree_rejects_case_insensitive_duplicate_destination(
    tmp_path: Path,
) -> None:
    package = _mixed_destination_package(
        tmp_path, "VehicleWorkbenchAxles/runtime.json",
    )
    (package / "other.json").write_text("{}", encoding="utf-8")
    manifest_path = package / "mod.toml"
    manifest_path.write_text(
        manifest_path.read_text(encoding="utf-8")
        + "[[files]]\n"
        + 'source = "other.json"\n'
        + 'destination = "vehicleworkbenchaxles/RUNTIME.JSON"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate destination"):
        ModManifest.load(package)


def test_sdk_runtime_tree_install_rejects_symlinked_parent_directory(
    tmp_path: Path,
) -> None:
    game = tmp_path / "game"
    game.mkdir()
    (game / "GTA5_Enhanced.exe").write_bytes(b"exe")
    outside = tmp_path / "outside-runtime"
    outside.mkdir()
    sentinel = outside / "sentinel.json"
    sentinel.write_bytes(b"protected")
    runtime_root = game / "VehicleWorkbenchAxles"
    try:
        runtime_root.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    package = _mixed_destination_package(
        tmp_path, "VehicleWorkbenchAxles/runtime.json",
    )
    manifest = ModManifest.load(package)
    service = mods.ModIntegrationService(game)

    with pytest.raises(ValueError, match="symlink or junction"):
        service.install(manifest)
    assert sentinel.read_bytes() == b"protected"
    assert not (service.state_root / "vehicle-workbench-runtime.json").exists()


def test_sdk_runtime_tree_install_rejects_predictable_temporary_symlink(
    tmp_path: Path,
) -> None:
    game = tmp_path / "game"
    game.mkdir()
    (game / "GTA5_Enhanced.exe").write_bytes(b"exe")
    runtime_root = game / "VehicleWorkbenchAxles"
    runtime_root.mkdir()
    sentinel = tmp_path / "outside-sentinel.json"
    sentinel.write_bytes(b"protected")
    legacy_temporary = runtime_root / ".runtime.json.allin1-install"
    try:
        legacy_temporary.symlink_to(sentinel)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    package = _mixed_destination_package(
        tmp_path, "VehicleWorkbenchAxles/runtime.json",
    )
    manifest = ModManifest.load(package)
    service = mods.ModIntegrationService(game)

    with pytest.raises(ValueError, match="legacy install-temporary"):
        service.install(manifest)
    assert sentinel.read_bytes() == b"protected"
    assert not (runtime_root / "runtime.json").exists()
    assert not (service.state_root / "vehicle-workbench-runtime.json").exists()


def test_sdk_runtime_tree_install_rejects_occupied_legacy_temporary_file(
    tmp_path: Path,
) -> None:
    game = tmp_path / "game"
    game.mkdir()
    (game / "GTA5_Enhanced.exe").write_bytes(b"exe")
    runtime_root = game / "VehicleWorkbenchAxles"
    runtime_root.mkdir()
    legacy_temporary = runtime_root / ".runtime.json.allin1-install"
    legacy_temporary.write_bytes(b"protected")
    package = _mixed_destination_package(
        tmp_path, "VehicleWorkbenchAxles/runtime.json",
    )
    manifest = ModManifest.load(package)
    service = mods.ModIntegrationService(game)

    with pytest.raises(ValueError, match="legacy install-temporary"):
        service.install(manifest)
    assert legacy_temporary.read_bytes() == b"protected"
    assert not (runtime_root / "runtime.json").exists()
    assert not (service.state_root / "vehicle-workbench-runtime.json").exists()
