"""Recipe UI contract and hostile-input tests; exclusively disposable trees."""
import json
import os
from pathlib import Path
import stat
import zipfile

import pytest

from allin1_sdk import recipe_desktop as recipe, workspace_desktop as desktop
from allin1_sdk.desktop_protocol import dispatch_operation
from allin1_sdk.mods import ModManifest
from test_sdk_tools import _oiv_folder, _oiv_archive


def request(source, output, action="managed", **extra):
    context = {"module": "recipe", "source": str(source)}
    _, session = dispatch_operation("inspect_authoring_workspace", context)
    payload = {**context, "action": action, "destination": str(output), "expected_state_sha256": session["state_sha256"], **extra}
    _, review = dispatch_operation("review_workspace_action", payload)
    assert not output.exists()
    return {**payload, "review_sha256": review["review_sha256"], "authoring_confirmed": True}


@pytest.mark.parametrize("archive", [False, True])
def test_managed_recipe_real_protocol_happy_path(tmp_path, archive):
    source = _oiv_archive(tmp_path) if archive else _oiv_folder(tmp_path)
    original = recipe._source_identity(source)
    output = tmp_path / "Converted package with spaces"
    _, receipt = dispatch_operation("apply_workspace_action", request(source, output))
    assert receipt["file_count"] == 3
    assert not receipt["game_write_performed"] and not receipt["archive_write_performed"]
    manifest = ModManifest.load(Path(receipt["reports"][0]))
    assert manifest.mod_id == "test-oiv" and len(manifest.files) == len(manifest.rpf_entries) == 1
    assert recipe._source_identity(source) == original
    assert receipt["output_sha256"] == recipe.digest(recipe._inventory(output))


def test_recipe_inspection_binds_request_spelling_to_canonical_source(tmp_path):
    source = _oiv_folder(tmp_path)
    # A trailing separator is portable; Windows also accepts forward slashes.
    selected = source.as_posix() + "/"
    _, session = dispatch_operation("inspect_authoring_workspace", {"module": "recipe", "source": selected})
    assert session["requested_source"] == selected
    assert session["source"] == str(source.resolve())
    assert session["state_sha256"] == recipe._source_identity(source)
    assert session["read_only"] and not session["game_write_performed"]


def test_recipe_path_aliases_do_not_bypass_source_or_request_validation(tmp_path):
    source = _oiv_folder(tmp_path)
    output = tmp_path / "not-created"
    pending = request(source.as_posix() + "/", output)
    # Even a path spelling change requires another review; consent stays exact.
    pending["source"] = str(source.resolve())
    with pytest.raises(ValueError, match="changed|match"):
        desktop.apply(pending)
    assert not output.exists()


def test_nested_batch_exports_complete_inert_recipe(tmp_path):
    source = _oiv_folder(tmp_path, '''<package><content><archive path="update/update.rpf"><archive path="child.rpf">
      <add source="data.xml">common/data/new.xml</add><delete>common/data/old.xml</delete>
    </archive></archive></content></package>''')
    _, receipt = dispatch_operation("apply_workspace_action", request(source, tmp_path / "batch", "batches"))
    assert receipt["inert_plan_only"]
    manifest = json.loads(Path(receipt["reports"][0]).read_text())
    assert [item["action"] for item in manifest["changes"]] == ["upsert", "delete"]
    assert {item["archive_path"] for item in manifest["changes"]} == {"child.rpf"}


def test_batch_does_not_claim_complete_conversion_for_omitted_filesystem_operations(tmp_path):
    source = _oiv_folder(tmp_path)
    assert not desktop.inspect({"module": "recipe", "source": str(source)})["capabilities"]["batches"]
    with pytest.raises(ValueError, match="does not support"):
        request(source, tmp_path / "not-created", "batches")


@pytest.mark.parametrize("mutate", ["source", "request", "confirmation"])
def test_changed_or_unconfirmed_recipe_never_writes(tmp_path, mutate):
    source = _oiv_folder(tmp_path)
    output = tmp_path / "not-created"
    pending = request(source, output)
    if mutate == "source":
        (source / "content" / "plugin.dll").write_bytes(b"changed since review")
    elif mutate == "request":
        pending["action"] = "created"
    else:
        pending["authoring_confirmed"] = False
    with pytest.raises(ValueError):
        desktop.apply(pending)
    assert not output.exists()


@pytest.mark.parametrize("name", ["../outside.txt", "/absolute", "C:/drive", "a\\b", "a/../../outside", "CON", "a:stream", "a. "])
def test_hostile_zip_members_rejected_before_output_writes(tmp_path, name):
    canary = tmp_path / "outside.txt"
    canary.write_text("unchanged")
    source = _oiv_archive(tmp_path)
    with zipfile.ZipFile(source, "a") as archive:
        # ZipInfo normalizes backslashes on Windows; retain the raw hostile
        # central-directory name rather than accidentally testing a safe path.
        item = zipfile.ZipInfo("unsafe")
        item.filename = name
        archive.writestr(item, b"hostile")
    with pytest.raises(ValueError):
        request(source, tmp_path / "not-created")
    assert canary.read_text() == "unchanged"
    assert not (tmp_path / "not-created").exists()


@pytest.mark.parametrize("kind", ["duplicate", "link", "file-parent"])
def test_alias_and_link_members_rejected(tmp_path, kind):
    source = _oiv_archive(tmp_path)
    with zipfile.ZipFile(source, "a") as archive:
        if kind == "duplicate":
            archive.writestr("CONTENT/DATA.XML", "alias")
        elif kind == "link":
            item = zipfile.ZipInfo("shortcut")
            item.create_system = 3
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(item, "../outside.txt")
        else:
            archive.writestr("content/plugin.dll/child", "conflict")
    with pytest.raises(ValueError):
        request(source, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


def test_output_must_be_new_separate_and_not_game_owned(tmp_path):
    source = _oiv_folder(tmp_path)
    game = tmp_path / "synthetic game"
    game.mkdir()
    (game / "GTA5.exe").write_bytes(b"MZ fixture only")
    for target in (source / "out", source, game / "out", tmp_path / ".." / "escape"):
        with pytest.raises(ValueError):
            request(source, target)


def test_oversized_plan_rejected_before_writes(tmp_path):
    operations = ''.join(f'<add source="plugin.dll">scripts/p{i}.dll</add>' for i in range(257))
    source = _oiv_folder(tmp_path, f'<package><content>{operations}</content></package>')
    with pytest.raises(ValueError, match="256 operations"):
        request(source, tmp_path / "not-created")


@pytest.mark.skipif(os.environ.get("ALLIN1_NATIVE_RPF_TEST") != "1", reason="explicit native RPF gate required")
def test_real_created_rpf_and_compiled_xml_bundle_preserve_input(tmp_path):
    source = _oiv_folder(tmp_path, '''<package version="2.2"><metadata><name>Owned recipe</name><gameversion>enhanced</gameversion></metadata><content>
      <archive path="update/x64/dlcpacks/owned/dlc.rpf" createIfNotExist="true"><add source="data.xml">config.xml</add></archive>
    </content></package>''')
    (source / "content/data.xml").write_text("<Root><Value>old</Value></Root>")
    game = tmp_path / "Synthetic decoder"
    game.mkdir()
    (game / "GTA5_Enhanced.exe").write_bytes(b"MZ synthetic context; not executable")
    output = tmp_path / "Created package"
    _, receipt = dispatch_operation("apply_workspace_action", request(source, output, "created", gta_path=str(game)))
    manifest = ModManifest.load(Path(receipt["reports"][0]))
    # Package payload names are prefixed; use an owned copy with the declared
    # destination basename, exactly as the compiler requires.
    archive = tmp_path / "dlc.rpf"
    archive.write_bytes((output / manifest.files[0].source).read_bytes())
    original = archive.read_bytes()
    (source / "assembly.xml").write_text('''<package version="2.2"><metadata><gameversion>enhanced</gameversion></metadata><content>
      <archive path="update/x64/dlcpacks/owned/dlc.rpf"><xml path="config.xml"><replace xpath="/Root/Value"><Value>new</Value></replace></xml></archive>
    </content></package>''')
    compiled = tmp_path / "Compiled bundle"
    _, result = dispatch_operation("apply_workspace_action", request(source, compiled, "compile", gta_path=str(game), archive=str(archive)))
    assert result["inert_plan_only"] and not result["archive_write_performed"]
    assert len(result["reports"]) == 2
    assert any(b"<Value>new</Value>" in item.read_bytes() for item in (compiled / "payloads").iterdir())
    assert archive.read_bytes() == original
    assert (source / "content/data.xml").read_text() == "<Root><Value>old</Value></Root>"
