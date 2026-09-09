import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from allin1_sdk.edition_bundle import build_edition_bundle
from allin1_sdk.mods import ModManifest, ModIntegrationService, open_mod_package
from allin1_sdk.cli import main
from allin1_sdk.agent_api import command_risk, execute_request


def oiv(root, edition, *, declared=None, unsupported=False):
    source = root / (edition + ".oiv")
    recipe = ('<delete>scripts/test.ini</delete>' if unsupported else
              '<add source="test.ini">scripts/test.ini</add>')
    with zipfile.ZipFile(source, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("assembly.xml", f'''<package version="2.2" target="Five">
          <metadata><name>Original {edition}</name><version><major>1</major><minor>0</minor></version>
          <gameVersion>{declared or edition}</gameVersion></metadata><content>{recipe}</content></package>''')
        archive.writestr("content/test.ini", edition.encode())
        archive.writestr("private/source.cs", "DO NOT DISTRIBUTE")
    return source


def build(root, **kwargs):
    options = dict(legacy=oiv(root, "legacy"), enhanced=oiv(root, "enhanced"),
                   mod_id="test.bundle", name="Test bundle", version="1.0")
    options.update(kwargs)
    return build_edition_bundle(root / "bundle.zip", **options)


@pytest.mark.parametrize("edition", ["legacy", "enhanced"])
def test_oiv_bundle_installs_selected_variant_and_restores(tmp_path, edition):
    result = build(tmp_path)
    archive = Path(result["path"])
    assert result["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive) as package:
        assert set(package.namelist()) == {"mod.toml", "legacy/mod.toml", "enhanced/mod.toml",
                                         "legacy/payload/001_test.ini", "enhanced/payload/001_test.ini"}
    game = tmp_path / "game"
    game.mkdir()
    (game / ("GTA5_Enhanced.exe" if edition == "enhanced" else "GTA5.exe")).write_bytes(b"exe")
    (game / "scripts").mkdir()
    (game / "scripts/test.ini").write_bytes(b"original")
    service = ModIntegrationService(game)
    with open_mod_package(archive) as manifest:
        review = service.review_install(manifest)
        assert review["ready"], review
        assert review["package"]["editions"] == [edition]
        service.install(manifest)
        assert (game / "scripts/test.ini").read_bytes() == edition.encode()
    service.uninstall("test.bundle")
    assert (game / "scripts/test.ini").read_bytes() == b"original"


def test_zip_of_two_oivs_and_launcher_acceptance(tmp_path):
    legacy, enhanced = oiv(tmp_path, "legacy"), oiv(tmp_path, "enhanced")
    outer = tmp_path / "download.zip"
    with zipfile.ZipFile(outer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(legacy, "Legacy/install.oiv")
        archive.write(enhanced, "Enhanced/install.oiv")
    result = build_edition_bundle(tmp_path / "ready.zip", source_zip=outer,
                                 legacy="Legacy/install.oiv", enhanced="Enhanced/install.oiv",
                                 mod_id="test.bundle", name="Test bundle", version="1.0")
    launcher = Path(__file__).resolve().parents[2] / "ALLIN1/src"
    if not launcher.is_dir():
        pytest.skip("Sibling launcher is unavailable")
    sys.path.insert(0, str(launcher))
    try:
        from allin1.mods import open_mod_package as launcher_open
        with launcher_open(result["path"]) as package:
            assert package.schema_version == 5
            for edition in ("legacy", "enhanced"):
                selected = package.for_edition(edition)
                assert (selected.package_root / selected.files[0].source).read_bytes() == edition.encode()
    finally:
        sys.path.remove(str(launcher))


@pytest.mark.parametrize("failure", ["wrong_edition", "unsupported", "missing_member", "same_member", "traversal"])
def test_invalid_oiv_inputs_leave_no_partial_output(tmp_path, failure):
    legacy = oiv(tmp_path, "legacy")
    enhanced = oiv(tmp_path, "enhanced", declared="legacy" if failure == "wrong_edition" else None,
                   unsupported=failure == "unsupported")
    options = dict(legacy=legacy, enhanced=enhanced)
    if failure in {"missing_member", "same_member", "traversal"}:
        source = tmp_path / "source.zip"
        with zipfile.ZipFile(source, "w") as archive:
            archive.write(legacy, "legacy.oiv")
            archive.write(enhanced, "enhanced.oiv")
            if failure == "traversal":
                archive.writestr("../outside.ini", "escape")
        options = dict(source_zip=source, legacy="legacy.oiv",
                       enhanced="missing.oiv" if failure == "missing_member" else
                       "legacy.oiv" if failure == "same_member" else "enhanced.oiv")
    with pytest.raises(ValueError):
        build_edition_bundle(tmp_path / "fail.zip", mod_id="test.bundle", name="Test bundle",
                             version="1.0", **options)
    assert not (tmp_path / "fail.zip").exists()
    assert not list(tmp_path.glob("allin1-editions-*"))


def test_existing_output_is_not_replaced(tmp_path):
    (tmp_path / "bundle.zip").write_bytes(b"user data")
    with pytest.raises(ValueError, match="already exists"):
        build(tmp_path)
    assert (tmp_path / "bundle.zip").read_bytes() == b"user data"


def test_managed_rebundle_excludes_unrelated_source(tmp_path):
    result = build(tmp_path)
    with open_mod_package(result["path"]) as manifest:
        (manifest.variants[0].package_root / "source.cs").write_text("PRIVATE")
        rebuilt = build_edition_bundle(tmp_path / "rebuilt.zip",
            legacy=manifest.variants[0].manifest_path, enhanced=manifest.variants[1].manifest_path,
            mod_id="test.bundle", name="Test bundle", version="1.0")
    with zipfile.ZipFile(rebuilt["path"]) as archive:
        assert not any("source.cs" in name for name in archive.namelist())


def test_cli_and_agent_catalog(tmp_path):
    legacy, enhanced = oiv(tmp_path, "legacy"), oiv(tmp_path, "enhanced")
    args = ["build-edition-bundle", "--legacy", str(legacy), "--enhanced", str(enhanced),
            "--id", "test.bundle", "--name", "Test bundle", "--version", "1.0",
            "--output", str(tmp_path / "cli.zip")]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["schema_version"] == 5
    assert command_risk("build-edition-bundle") == "authoring_write"
    validated = CliRunner().invoke(main, ["validate-package", str(tmp_path / "cli.zip"),
                                        "--edition", "legacy"])
    assert validated.exit_code == 0, validated.output
    assert json.loads(validated.output)["editions"] == ["legacy"]
    request = execute_request({
        "id": "bundle-export", "action": "execute", "command": "build-edition-bundle",
        "parameters": {"legacy": str(legacy), "enhanced": str(enhanced),
                       "mod_id": "test.bundle", "name": "Test bundle", "version": "1.0",
                       "output": str(tmp_path / "agent.zip")},
    }, audit_path=tmp_path / "audit.jsonl")
    assert request["ok"], request
    assert request["risk"] == "authoring_write"
    assert Path(tmp_path / "agent.zip").is_file()


def test_contract_stays_identical_to_launcher():
    sdk = Path(__file__).resolve().parents[1] / "src/allin1_sdk/mod_package_contract.py"
    launcher = sdk.parents[3] / "ALLIN1/src/allin1/mod_package_contract.py"
    if launcher.is_file():
        assert sdk.read_bytes() == launcher.read_bytes()


def test_game_destination_is_protected(tmp_path):
    (tmp_path / "GTA5_Enhanced.exe").write_bytes(b"fixture")
    with pytest.raises(ValueError, match="outside the game"):
        build(tmp_path)
    assert not (tmp_path / "bundle.zip").exists()


def test_failed_publication_leaves_no_partial_zip(tmp_path, monkeypatch):
    import allin1_sdk.edition_bundle as module
    def fail(*args):
        raise OSError("Publication unavailable")
    monkeypatch.setattr(module.os, "link", fail)
    with pytest.raises(OSError, match="Publication unavailable"):
        build(tmp_path)
    assert not (tmp_path / "bundle.zip").exists()
    assert not list(tmp_path.glob("allin1-editions-*"))
