from dataclasses import replace

import pytest

from allin1_sdk import weapon_desktop
from allin1_sdk.addon_importer import AddonPackageInspector, PackageEntry
from allin1_sdk.weapon_authoring import WeaponAuthoringWorkspace
from allin1_sdk.weapon_native_preview import MAX_ARCHETYPE_BYTES, native_preview
from test_weapon_authoring_core import _source
from test_weapon_desktop import tree_hashes


def _assets(source, *names):
    for name in names:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"native fixture; not decoded by link inspection")


def _archetypes(*pairs):
    return ("<CWeaponModelInfo__InitDataList><InitDatas>" + "".join(
        f"<Item><modelName>{model}</modelName><txdName>{txd}</txdName></Item>" for model, txd in pairs
    ) + "</InitDatas></CWeaponModelInfo__InitDataList>").encode()


def test_exact_base_and_hi_links_do_not_include_substring_assets(tmp_path):
    source = _source(tmp_path)
    _assets(source, "stream/w_pi_author.ydr", "stream/w_pi_author_hi.ydr", "stream/w_pi_author_clip.ydr",
            "stream/w_pi_author_alt.ydr", "stream/w_pi_author.ytd")
    before = tree_hashes(source)
    result = weapon_desktop.inspect({"source": str(source)})["native_preview"]
    body, clip, scope, supp = result["parts"]
    assert [asset["path"] for asset in body["assets"]] == ["stream/w_pi_author.ydr", "stream/w_pi_author_hi.ydr"]
    assert all(asset["texture_entry"] == "stream/w_pi_author.ytd" for asset in body["assets"])
    assert len(clip["assets"]) == 1 and clip["default"]
    assert scope["assets"][0]["path"] == "stream/w_at_author_scope.ydr"
    assert supp["attach_bones"] == ["WAPSupp"]
    assert tree_hashes(source) == before


def test_shared_declared_texture_and_duplicate_paths_are_explicit(tmp_path):
    source = _source(tmp_path)
    _assets(source, "a/w_pi_author_clip.ydr", "b/w_pi_author_clip.ydr", "a/shared_body.ytd")
    (source / "weaponarchetypes.meta").write_bytes(_archetypes(("w_pi_author_clip", "shared_body")))
    payload = {"source": str(source), "editor_kind": "component", "component": "COMPONENT_AUTHOR_CLIP"}
    result = weapon_desktop.inspect(payload)["native_preview"]
    assert result["selected_part"] == "component:COMPONENT_AUTHOR_CLIP"
    clip = result["parts"][1]
    assert len(clip["assets"]) == 3
    assert all(asset["texture_entry"] == "a/shared_body.ytd" for asset in clip["assets"])
    _assets(source, "b/shared_body.ytd")
    result = weapon_desktop.inspect(payload)["native_preview"]
    assert all(asset["texture_entry"] is None and len(asset["texture_entries"]) == 2 for asset in result["parts"][1]["assets"])


def test_conflicting_declarations_do_not_guess_the_only_available_dictionary(tmp_path):
    source = _source(tmp_path)
    _assets(source, "w_pi_author.ydr", "shared.ytd")
    (source / "weaponarchetypes.meta").write_bytes(_archetypes(("w_pi_author", "shared"), ("w_pi_author", "external")))
    result = weapon_desktop.inspect({"source": str(source)})["native_preview"]
    assert result["parts"][0]["assets"][0]["texture_entry"] is None
    assert "Conflicting" in result["warnings"][0]


@pytest.mark.parametrize("content", [
    b"<broken>", b" " * 5000 + b'<!DOCTYPE root [<!ENTITY x "oops">]><root/>' ,
    '<!DOCTYPE root [<!ENTITY x "oops">]><root/>'.encode("utf-16"),
    _archetypes(("w_pi_author", "../../outside")), b"x" * (MAX_ARCHETYPE_BYTES + 1), None,
], ids=["malformed", "late-dtd", "utf16-dtd", "unsafe-name", "oversize", "unavailable"])
def test_unsafe_unavailable_and_oversized_archetypes_are_not_followed(tmp_path, content):
    source = _source(tmp_path)
    _assets(source, "w_pi_author.ydr")
    scan = AddonPackageInspector().inspect(source)
    scan = replace(scan, entries=scan.entries + (PackageEntry("weaponarchetypes.meta", len(content or b""), content),))
    result = native_preview(scan, "WEAPON_AUTHOR")
    assert result["warnings"]
    assert not result["parts"][0]["assets"][0]["texture_entries"]


def test_missing_and_ambiguous_component_definitions_never_resolve_a_model(tmp_path):
    source = _source(tmp_path)
    scan = AddonPackageInspector().inspect(source)
    clip = scan.weapon_components[0]
    scan = replace(scan, weapon_components=(clip, clip))
    result = native_preview(scan, "WEAPON_AUTHOR")
    assert "Multiple definitions" in result["parts"][1]["reason"]
    assert "definition is not bundled" in result["parts"][2]["reason"]
    assert all(not part["assets"] for part in result["parts"][1:])


def test_copied_workspace_preview_uses_only_copied_source(tmp_path):
    source = _source(tmp_path)
    _assets(source, "stream/w_pi_author.ydr")
    workspace = WeaponAuthoringWorkspace.create(source, tmp_path / "copy")
    _assets(source, "stream/w_pi_author_hi.ydr")
    result = weapon_desktop.inspect({"workspace": str(workspace.root), "editor_kind": "attachment",
                                     "weapon": "WEAPON_AUTHOR", "component": "COMPONENT_AUTHOR_SCOPE"})
    assert result["source"] == str(workspace.source)
    assert result["revision"] == 0
    assert result["native_preview"]["selected_part"] == "component:COMPONENT_AUTHOR_SCOPE"
    assert len(result["native_preview"]["parts"][0]["assets"]) == 1


def test_texture_choices_are_bounded_and_reported(tmp_path):
    scan = AddonPackageInspector().inspect(_source(tmp_path))
    scan = replace(scan, entries=scan.entries + tuple(PackageEntry(f"{i}.ytd", 1) for i in range(501)))
    result = native_preview(scan, "WEAPON_AUTHOR")
    assert len(result["texture_entries"]) == 500
    assert "limited" in result["warnings"][0]
