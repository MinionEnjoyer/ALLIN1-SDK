"""Real ZIP/manifest publication over verified, controlled RPF fixtures."""
import json
from pathlib import Path
import zipfile

import pytest

from allin1_sdk import gxt2_desktop as desktop, rpf_package_publication as publisher
from allin1_sdk.gxt2_workspace import Gxt2Workspace
from allin1_sdk.mods import open_mod_package
from test_gxt2_rpf_package import workspace, pending as rpf_pending


@pytest.fixture
def prepared(workspace, tmp_path):
    context, original, game = workspace
    payload, _ = rpf_pending(context, tmp_path / "rpf-build")
    result = desktop.apply(payload)
    metadata = {"id": "test.text", "name": 'Text "日本語"', "version": "1.2.3", "author": "Test author", "target": "mods/update/source.rpf"}
    request = {**context, "action": "publish_rpf", "source_package": result["destination"], "package_metadata": metadata,
               "destination": str(tmp_path / "package.zip"), "expected_state_sha256": desktop.inspect(context)["state_sha256"]}
    return request, original, game


def reviewed(request):
    value = desktop.review(request)
    return {**request, "review_sha256": value["review_sha256"], "authoring_confirmed": True}, value


def test_exports_exact_portable_zip_and_launcher_manifest(prepared, monkeypatch):
    request, original, game = prepared
    before = original.read_bytes()
    payload, review = reviewed(request)
    result = desktop.apply(payload)
    assert result["kind"] == "gxt2_rpf_published" and not result["game_write_performed"] and not result["install_performed"]
    assert result["sha256"] == publisher._hash(Path(result["archive"]))
    assert original.read_bytes() == before and list(game.iterdir()) == []
    with zipfile.ZipFile(result["archive"]) as package:
        assert package.namelist() == [row["path"] for row in review["rpf_publication"]["members"]]
        for name in ["mod.toml", "allin1.rpf-build.json", "README.txt"]:
            text = package.read(name).decode()
            assert request["workspace"] not in text and str(game) not in text and "C:\\Users" not in text
        assert "ENTIRE RPF" in package.read("README.txt").decode()
    with open_mod_package(result["archive"]) as manifest:
        assert manifest.schema_version == 1 and manifest.mod_type == "rpf"
        assert manifest.name == 'Text "日本語"' and manifest.editions == ("enhanced",)
        assert manifest.dependencies == ("openrpf",) and not manifest.dlc_packs and not manifest.rpf_entries
        assert manifest.files[0].destination.as_posix() == request["package_metadata"]["target"]
        assert manifest.files[0].sha256 == result["payload_sha256"]
    launcher = Path(__file__).resolve().parents[2] / "ALLIN1" / "src"
    if launcher.is_dir():
        monkeypatch.syspath_prepend(str(launcher))
        from allin1.mods import open_mod_package as launcher_open
        with launcher_open(result["archive"]) as manifest:
            assert manifest.mod_id == "test.text" and manifest.files[0].sha256 == result["payload_sha256"]


@pytest.mark.parametrize("target", ["update/source.rpf", "mods/../source.rpf", "mods/C:/source.rpf", "mods/CON/source.rpf", "mods/update/other.rpf", "mods/update/source.rpf!nested.rpf", "mods//source.rpf"])
def test_rejects_unsafe_or_renamed_archive_targets(prepared, target):
    request, _, _ = prepared
    request["package_metadata"]["target"] = target
    with pytest.raises(ValueError): desktop.review(request)
    assert not Path(request["destination"]).exists()


@pytest.mark.parametrize("field,value", [("id", "allin1.private"), ("id", "UpperCase"), ("author", ""), ("name", "bad\nname"), ("version", " " * 65)])
def test_rejects_invalid_package_metadata(prepared, field, value):
    request, _, _ = prepared
    request["package_metadata"][field] = value
    with pytest.raises(ValueError): desktop.review(request)


@pytest.mark.parametrize("change", ["workspace", "archive", "report", "dictionary", "validation", "metadata", "output", "confirmation"])
def test_stale_or_unconfirmed_export_does_not_publish(prepared, change, tmp_path):
    request, _, _ = prepared
    payload, _ = reviewed(request)
    source = Path(request["source_package"])
    if change == "workspace": Gxt2Workspace.set_text(request["workspace"], 256, "New revision")
    elif change == "archive": (source / "archive/source.rpf").write_bytes(b"RPF7 changed")
    elif change == "report": (source / "rpf-package.json").write_text("{}")
    elif change == "dictionary": (source / "payload/replacement.gxt2").write_bytes(b"bad")
    elif change == "validation": (source / "payload/replacement.gxt2.gxt2-validation.json").write_text("{}")
    elif change == "metadata": payload["package_metadata"] = {**payload["package_metadata"], "target": "mods/other/source.rpf"}
    elif change == "output": payload["destination"] = str(tmp_path / "other.zip")
    else: payload["authoring_confirmed"] = False
    with pytest.raises((ValueError, OSError)): desktop.apply(payload)
    assert not Path(request["destination"]).exists() and not (tmp_path / "other.zip").exists()


@pytest.mark.parametrize("failure", ["corrupt_zip", "destination_race", "state_during_build", "source_during_build"])
def test_failed_export_cleans_staging_without_overwriting_user_files(prepared, monkeypatch, failure, tmp_path):
    request, _, _ = prepared
    payload, _ = reviewed(request)
    native_open = publisher.open_mod_package
    from contextlib import contextmanager
    @contextmanager
    def inject(path):
        with native_open(path) as manifest:
            yield manifest
        if failure == "corrupt_zip": raise ValueError("Injected validation failure")
        if failure == "destination_race": Path(request["destination"]).write_bytes(b"user file")
        if failure == "state_during_build":
            # An out-of-process edit bypasses the already-held workspace lock.
            entry_file = Path(request["workspace"]) / "entries.json"
            data = json.loads(entry_file.read_text()); data[0]["text"] = "Concurrent"; entry_file.write_text(json.dumps(data))
        if failure == "source_during_build": (Path(request["source_package"]) / "rpf-package.json").write_text("{}")
    monkeypatch.setattr(publisher, "open_mod_package", inject)
    with pytest.raises((ValueError, OSError, RuntimeError)): desktop.apply(payload)
    if failure == "destination_race": assert Path(request["destination"]).read_bytes() == b"user file"
    else: assert not Path(request["destination"]).exists()
    assert not list(tmp_path.glob(".allin1-rpf-publish-*"))


def test_blocks_game_workspace_and_source_outputs_and_existing_zip(prepared):
    request, _, game = prepared
    for parent in [game, Path(request["workspace"]), Path(request["source_package"])]:
        with pytest.raises(ValueError): desktop.review({**request, "destination": str(parent / "out.zip")})
    Path(request["destination"]).write_bytes(b"keep")
    with pytest.raises(ValueError): desktop.review(request)
    assert Path(request["destination"]).read_bytes() == b"keep"


def test_rechecks_compatibility_limits_and_disk_space(prepared, monkeypatch):
    request, _, _ = prepared
    with monkeypatch.context() as context:
        context.setattr(publisher, "MAX_PACKAGE_ARCHIVE_MEMBER_BYTES", 10)
        with pytest.raises(ValueError, match="oversized"): desktop.review(request)
    usage = publisher.shutil.disk_usage(Path(request["destination"]).parent)
    monkeypatch.setattr(publisher.shutil, "disk_usage", lambda path: usage._replace(free=0))
    with pytest.raises(ValueError, match="disk space"): desktop.review(request)


def test_publication_does_not_require_original_archive_or_running_game_context(prepared):
    request, original, _ = prepared
    original.unlink()
    payload, _ = reviewed(request)
    assert desktop.apply(payload)["kind"] == "gxt2_rpf_published"


def test_member_mode_never_falls_back_to_whole_archive_for_nested_workspace(prepared):
    request, _, _ = prepared
    member_request = {**request, "publication_mode": "member"}
    if desktop.inspect({"workspace": request["workspace"]})["source_binding"]["entry_id"].startswith("::"):
        assert desktop.review(member_request)["rpf_publication"]["manifest_schema_version"] == 3
    else:
        publication = desktop.review(member_request)["rpf_publication"]
        assert publication["manifest_schema_version"] == 4
        assert publication["entry"] == "american.rpf!global.gxt2"
        assert not publication["whole_archive_replacement"]
    assert not Path(request["destination"]).exists()


def test_launcher_installs_and_restores_only_a_temporary_game_target(prepared, monkeypatch, tmp_path):
    launcher = Path(__file__).resolve().parents[2] / "ALLIN1" / "src"
    if not launcher.is_dir(): pytest.skip("Sibling Launcher checkout is unavailable")
    monkeypatch.syspath_prepend(str(launcher))
    from allin1.mods import ModIntegrationService, open_mod_package as launcher_open
    request, _, game = prepared
    payload, _ = reviewed(request)
    result = desktop.apply(payload)
    assert game.resolve().is_relative_to(tmp_path.resolve())
    (game / "GTA5_Enhanced.exe").write_bytes(b"test marker, not executable")
    target = game / request["package_metadata"]["target"]
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original temporary archive")
    service = ModIntegrationService(game)
    # Only loader availability is simulated; install/receipt/backup/restore run
    # through the Launcher domain on the temporary test directory.
    monkeypatch.setattr(service, "_check_dependencies", lambda manifest: None)
    with launcher_open(result["archive"]) as manifest:
        installed = service.install(manifest)
        assert installed.mod_id == "test.text"
        assert publisher._hash(target) == result["payload_sha256"]
    receipt = json.loads(service._receipt_path("test.text").read_text())
    assert len(receipt["files"]) == 1 and not receipt["rpf_entries"]
    service.uninstall("test.text")
    assert target.read_bytes() == b"original temporary archive"
    assert not service._receipt_path("test.text").exists()
