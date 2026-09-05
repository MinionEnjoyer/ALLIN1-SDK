"""Member-only export shares the existing publication guards, never the archive payload."""
import json
from pathlib import Path
import zipfile

import pytest

from allin1_sdk import gxt2_desktop as desktop
from allin1_sdk.mods import open_mod_package
from test_gxt2_rpf_package import workspace
from test_rpf_package_publication import (
    prepared as whole_prepared, reviewed,
    test_stale_or_unconfirmed_export_does_not_publish,
    test_failed_export_cleans_staging_without_overwriting_user_files,
    test_blocks_game_workspace_and_source_outputs_and_existing_zip,
    test_rechecks_compatibility_limits_and_disk_space,
    test_rejects_invalid_package_metadata,
    test_rejects_unsafe_or_renamed_archive_targets,
    test_publication_does_not_require_original_archive_or_running_game_context,
)

pytestmark = pytest.mark.parametrize("workspace", ["::global.gxt2", "american.rpf::global.gxt2"], indirect=True)


@pytest.fixture
def prepared(whole_prepared):
    request, original, game = whole_prepared
    return {**request, "publication_mode": "member"}, original, game


def test_member_zip_contains_only_dictionary_and_exact_original_precondition(prepared, monkeypatch):
    request, original, game = prepared
    before = original.read_bytes()
    binding = desktop.inspect({"workspace": request["workspace"]})["source_binding"]["entry_id"]
    schema = 3 if binding.startswith("::") else 4
    target_entry = binding[2:] if schema == 3 else binding.replace("::", "!")
    payload, review = reviewed(request)
    publication = review["rpf_publication"]
    assert publication["publication_mode"] == "member" and not publication["whole_archive_replacement"]
    assert publication["manifest_schema_version"] == schema and publication["entry"] == target_entry
    assert publication["original_sha256"] == desktop.inspect({"workspace": request["workspace"]})["original_sha256"]
    result = desktop.apply(payload)
    assert result["publication_mode"] == "member" and result["original_sha256"] == publication["original_sha256"]
    assert result["payload_sha256"] == publication["payload_sha256"]
    with zipfile.ZipFile(result["archive"]) as archive:
        assert archive.namelist() == ["README.txt", "allin1.rpf-build.json", "mod.toml", "payload/replacement.gxt2"]
        assert archive.read("payload/replacement.gxt2") == (Path(request["source_package"]) / "payload/replacement.gxt2").read_bytes()
        readme = archive.read("README.txt").decode()
        assert "Older Launchers reject" in readme and "Do not downgrade" in readme
        evidence = json.loads(archive.read("allin1.rpf-build.json"))
        assert not evidence["whole_archive_replacement"] and evidence["manifest_schema_version"] == schema
        for name in ("README.txt", "mod.toml", "allin1.rpf-build.json"):
            assert str(game) not in archive.read(name).decode() and request["workspace"] not in archive.read(name).decode()
    with open_mod_package(result["archive"]) as manifest:
        assert manifest.schema_version == schema and not manifest.files and len(manifest.rpf_entries) == 1
        entry = manifest.rpf_entries[0]
        assert entry.original_sha256 == publication["original_sha256"] and entry.sha256 == result["payload_sha256"]
        assert entry.entry.as_posix() == target_entry and entry.archive.as_posix() == request["package_metadata"]["target"]
    launcher = Path(__file__).resolve().parents[2] / "ALLIN1" / "src"
    if launcher.is_dir():
        monkeypatch.syspath_prepend(str(launcher))
        from allin1.mods import open_mod_package as launcher_open
        with launcher_open(result["archive"]) as manifest:
            assert manifest.schema_version == schema and manifest.rpf_entries[0].original_sha256 == entry.original_sha256
    assert original.read_bytes() == before and not list(game.iterdir())


def test_changed_scope_requires_new_review(prepared):
    request, _, _ = prepared
    payload, _ = reviewed(request)
    payload["publication_mode"] = "whole_archive"
    with pytest.raises(ValueError): desktop.apply(payload)
    assert not Path(request["destination"]).exists()


@pytest.mark.parametrize("mode", [None, True, "nested", "", {}])
def test_unknown_publication_modes_fail_closed(prepared, mode):
    request, _, _ = prepared
    with pytest.raises(ValueError): desktop.review({**request, "publication_mode": mode})
