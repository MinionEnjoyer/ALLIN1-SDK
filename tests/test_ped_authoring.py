from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner
from lxml import etree

from allin1_sdk.addon_importer import AddonPackageInspector, PackageFinding
from allin1_sdk.agent_api import command_catalog, execute_request
from allin1_sdk.cli import main
import allin1_sdk.ped_authoring as ped_authoring_module
from allin1_sdk.ped_authoring import (
    PedAuthoringWorkspace,
    PedClonePlan,
    PedCloneSpec,
)


PEDS_META = """<?xml version="1.0" encoding="UTF-8"?>
<CPedModelInfo__InitDataList>
  <!-- preserve-ped-comment -->
  <InitDatas>
    <Item type="CPedModelInfo__InitData">
      <Name>ig_author</Name>
      <Pedtype>PERSON</Pedtype>
      <ModelType value="human" />
      <PropsName>ig_author_p</PropsName>
      <ClipDictionaryName ref="move_m@generic" />
      <ExpressionSetName>expr_set_ambient_male</ExpressionSetName>
      <MovementClipSet>move_m@casual@d</MovementClipSet>
      <CreatureMetadataName>METADATA_HUMAN_MALE</CreatureMetadataName>
      <UnknownPedField mode="keep"><Nested value="42" /></UnknownPedField>
    </Item>
  </InitDatas>
</CPedModelInfo__InitDataList>
"""


def _ped_package(root: Path, *, omit_expression: bool = False) -> Path:
    package = root / "ped-package"
    package.mkdir()
    metadata = PEDS_META
    if omit_expression:
        metadata = metadata.replace(
            "      <ExpressionSetName>expr_set_ambient_male</ExpressionSetName>\n",
            "",
        )
    (package / "peds.meta").write_text(metadata, encoding="utf-8")
    stream = package / "stream"
    stream.mkdir()
    (stream / "ig_author.ydd").write_bytes(b"ped-drawable")
    (stream / "ig_author.ytd").write_bytes(b"ped-texture")
    (stream / "ig_author_p.ydd").write_bytes(b"prop-drawable")
    (stream / "ig_author_p.ytd").write_bytes(b"prop-texture")
    (stream / "ig_clone.ydd").write_bytes(b"clone-drawable")
    (stream / "ig_clone.ytd").write_bytes(b"clone-texture")
    (stream / "ig_clone_p.ydd").write_bytes(b"clone-prop-drawable")
    (stream / "ig_clone_p.ytd").write_bytes(b"clone-prop-texture")
    return package


def _workspace(tmp_path: Path) -> PedAuthoringWorkspace:
    return PedAuthoringWorkspace.create(
        _ped_package(tmp_path), tmp_path / "ped-workspace",
    )




def test_ped_workspace_copies_edits_preserves_xml_and_undoes(tmp_path):
    source = _ped_package(tmp_path)
    original = (source / "peds.meta").read_bytes()
    workspace = PedAuthoringWorkspace.create(source, tmp_path / "workspace")

    assert workspace.revision == 0
    assert workspace.values("IG_AUTHOR").values["ped.pedType"] == "PERSON"
    result = workspace.update(
        "ig_author",
        {
            "ped.modelType": "animal",
            "ped.clipDictionary": "move_m@brave",
            "ped.movementClipSet": "move_m@brave",
        },
        expected_revision=0,
    )
    assert result.revision == 1
    assert result.history.is_dir()
    assert (source / "peds.meta").read_bytes() == original
    values = workspace.values("ig_author").values
    assert values["ped.modelType"] == "animal"
    assert values["ped.clipDictionary"] == "move_m@brave"

    tree = etree.parse(str(workspace.source / "peds.meta"))
    assert tree.xpath("string(//ModelType/@value)") == "animal"
    assert tree.xpath("string(//ClipDictionaryName/@ref)") == "move_m@brave"
    assert tree.xpath("string(//UnknownPedField/@mode)") == "keep"
    assert tree.xpath("string(//UnknownPedField/Nested/@value)") == "42"
    assert tree.xpath("count(//comment()[contains(., 'preserve-ped-comment')])") == 1

    undone = workspace.undo(expected_revision=1)
    assert undone.revision == 2
    restored = workspace.values("ig_author").values
    assert restored["ped.modelType"] == "human"
    assert restored["ped.clipDictionary"] == "move_m@generic"


def test_ped_workspace_creation_requires_visible_ped_records(tmp_path):
    source = tmp_path / "empty-package"
    source.mkdir()
    (source / "readme.txt").write_text("no ped metadata", encoding="utf-8")
    with pytest.raises(ValueError, match="requires visible peds.meta records"):
        PedAuthoringWorkspace.create(source, tmp_path / "workspace")


@pytest.mark.parametrize(
    ("mutation", "message"),
    (("size", "inventory does not match"), ("identity", "records do not match")),
)
def test_ped_workspace_revalidates_the_actual_copied_tree(
    tmp_path, monkeypatch, mutation, message,
):
    source = _ped_package(tmp_path)

    def corrupt_copy(
        _source, destination, _scan, *, validate_copy, **_kwargs,
    ):
        copied = tmp_path / f"corrupt-{mutation}"
        shutil.copytree(source, copied)
        metadata = copied / "peds.meta"
        if mutation == "size":
            metadata.write_text(
                metadata.read_text(encoding="utf-8") + "\n<!-- changed -->",
                encoding="utf-8",
            )
        else:
            metadata.write_text(
                metadata.read_text(encoding="utf-8").replace(
                    "ig_author", "ig_buthor", 1,
                ),
                encoding="utf-8",
            )
        validate_copy(copied)
        return destination

    monkeypatch.setattr(
        ped_authoring_module, "create_copied_workspace", corrupt_copy,
    )
    with pytest.raises(RuntimeError, match=message):
        PedAuthoringWorkspace.create(source, tmp_path / "workspace")


def test_ped_authoring_refuses_missing_nodes_bad_fields_and_stale_revisions(tmp_path):
    source = _ped_package(tmp_path, omit_expression=True)
    workspace = PedAuthoringWorkspace.create(source, tmp_path / "workspace")
    before = (workspace.source / "peds.meta").read_bytes()

    with pytest.raises(ValueError, match="does not synthesize"):
        workspace.update("ig_author", {"ped.expressionSet": "expr_new"})
    with pytest.raises(ValueError, match="Unsupported ped authoring fields"):
        workspace.update("ig_author", {"ped.Name": "ig_renamed"})
    with pytest.raises(ValueError, match="may not be empty"):
        workspace.update("ig_author", {"ped.pedType": ""})
    assert workspace.revision == 0
    assert (workspace.source / "peds.meta").read_bytes() == before
    assert not list((workspace.root / "history").iterdir())

    workspace.update("ig_author", {"ped.propsName": ""}, expected_revision=0)
    with pytest.raises(ValueError, match="revision conflict"):
        workspace.update(
            "ig_author", {"ped.propsName": "ig_author_p"}, expected_revision=0,
        )


def test_ped_post_commit_failure_rolls_back_and_tampered_undo_is_rejected(
    tmp_path, monkeypatch,
):
    workspace = _workspace(tmp_path)
    before = (workspace.source / "peds.meta").read_bytes()

    def reject(*_args, **_kwargs):
        raise RuntimeError("forced ped verification failure")

    monkeypatch.setattr(workspace, "_verify_values", reject)
    with pytest.raises(RuntimeError, match="forced ped verification"):
        workspace.update("ig_author", {"ped.pedType": "CIVMALE"})
    assert workspace.revision == 0
    assert (workspace.source / "peds.meta").read_bytes() == before
    assert not list((workspace.root / "history").iterdir())

    monkeypatch.undo()
    workspace.update("ig_author", {"ped.pedType": "CIVMALE"})
    with (workspace.source / "peds.meta").open("ab") as stream:
        stream.write(b"\n<!-- external change -->\n")
    with pytest.raises(ValueError, match="changed after its edit"):
        workspace.undo(expected_revision=1)
    assert workspace.revision == 1


def test_ped_clone_requires_reviewed_assets_preserves_unknown_xml_and_undoes(
    tmp_path,
):
    workspace = _workspace(tmp_path)
    plan = workspace.plan_ped_clone(
        "ig_author", ped_name="ig_clone",
        updates={"ped.pedType": "CIVMALE"},
    )
    assert plan.ready is True
    assert plan.spec.updates["ped.propsName"] == "ig_clone_p"
    assert set(plan.selected_sources) == {
        "ped_metadata", "model_drawable", "model_texture",
        "props_drawable", "props_texture",
    }
    assert len(plan.plan_sha256) == 64

    result = workspace.clone_ped_bundle(
        plan, expected_revision=0, expected_plan_sha256=plan.plan_sha256,
    )
    assert result.revision == 1
    values = workspace.values("ig_clone").values
    assert values["ped.pedType"] == "CIVMALE"
    assert values["ped.propsName"] == "ig_clone_p"
    tree = etree.parse(str(workspace.source / "peds.meta"))
    clone = tree.xpath("//Item[Name='ig_clone']")[0]
    assert clone.xpath("string(UnknownPedField/@mode)") == "keep"
    assert clone.xpath("string(UnknownPedField/Nested/@value)") == "42"
    assert workspace.manifest["created_records"][0]["name"] == "ig_clone"

    undone = workspace.undo(expected_revision=1)
    assert undone.revision == 2
    with pytest.raises(ValueError, match="not found uniquely"):
        workspace.values("ig_clone")
    assert workspace.manifest["created_records"] == []


def test_ped_clone_rejects_missing_assets_and_stale_plan(tmp_path):
    workspace = _workspace(tmp_path)
    blocked = workspace.plan_ped_clone(
        "ig_author", ped_name="ig_missing",
    )
    assert blocked.ready is False
    codes = {item.code for item in blocked.findings}
    assert "target_model_drawable_not_unique" in codes
    assert "target_model_texture_not_unique" in codes

    reviewed = workspace.plan_ped_clone("ig_author", ped_name="ig_clone")
    workspace.update("ig_author", {"ped.pedType": "CIVMALE"})
    with pytest.raises(ValueError, match="revision conflict"):
        workspace.clone_ped_bundle(
            reviewed, expected_revision=0,
            expected_plan_sha256=reviewed.plan_sha256,
        )


def test_separate_workspace_clients_refresh_revision_before_writing(tmp_path):
    first = _workspace(tmp_path)
    second = PedAuthoringWorkspace(first.root)
    reviewed = first.plan_ped_clone("ig_author", ped_name="ig_clone")
    second.update(
        "ig_author", {"ped.expressionSet": "expr_updated_elsewhere"},
        expected_revision=0,
    )
    with pytest.raises(ValueError, match="revision conflict"):
        first.clone_ped_bundle(
            reviewed, expected_revision=0,
            expected_plan_sha256=reviewed.plan_sha256,
        )
    assert first.revision == 1
    assert first.values("ig_author").values["ped.expressionSet"] \
        == "expr_updated_elsewhere"


def test_ped_clone_plan_reports_incomplete_donor_collisions_and_ambiguous_assets(
    tmp_path,
):
    source = _ped_package(tmp_path, omit_expression=True)
    (source / "stream" / "ig_clone.ydr").write_bytes(b"second-drawable")
    workspace = PedAuthoringWorkspace.create(source, tmp_path / "workspace")

    incomplete = workspace.plan_ped_clone("ig_author", ped_name="ig_clone")
    codes = {item.code for item in incomplete.findings}
    assert "donor_ped_incomplete" in codes
    assert "target_model_drawable_not_unique" in codes
    assert incomplete.ready is False

    collision = workspace.plan_ped_clone(
        "ig_author", ped_name="ig_author",
        updates={"ped.propsName": "ig_author_p"},
    )
    assert "target_ped_exists" in {item.code for item in collision.findings}
    assert collision.ready is False

    with pytest.raises(ValueError, match="not found uniquely"):
        workspace.plan_ped_clone("ig_missing", ped_name="ig_clone")
    direct = workspace._plan_ped_clone_locked(
        AddonPackageInspector().inspect(workspace.source),
        PedCloneSpec("ig_missing", "ig_clone", {"ped.propsName": "ig_clone_p"}),
    )
    assert "donor_ped_not_unique" in {item.code for item in direct.findings}


def test_ped_clone_plan_validates_fields_and_supports_shared_model_props(tmp_path):
    workspace = _workspace(tmp_path)
    with pytest.raises(ValueError, match="Unsupported ped clone fields"):
        workspace.plan_ped_clone(
            "ig_author", ped_name="ig_clone", updates={"ped.Name": "bad"},
        )
    with pytest.raises(ValueError, match="safe game identifier"):
        workspace.plan_ped_clone("ig_author", ped_name="../escape")
    with pytest.raises(ValueError, match="may not be empty"):
        workspace.plan_ped_clone(
            "ig_author", ped_name="ig_clone", updates={"ped.pedType": ""},
        )

    plan = workspace.plan_ped_clone(
        "ig_author", ped_name="ig_clone",
        updates={"ped.propsName": "ig_clone"},
    )
    assert plan.ready
    assert set(plan.selected_sources) == {
        "ped_metadata", "model_drawable", "model_texture",
    }
    assert plan.plan_sha256 == workspace.plan_ped_clone(
        "ig_author", ped_name="ig_clone",
        updates={"ped.propsName": "ig_clone"},
    ).plan_sha256


def test_ped_clone_execution_rejects_bad_digest_payload_and_blocked_plan(tmp_path):
    workspace = _workspace(tmp_path)
    plan = workspace.plan_ped_clone("ig_author", ped_name="ig_clone")
    with pytest.raises(ValueError, match="64 lowercase hex"):
        workspace.clone_ped_bundle(
            plan, expected_revision=0, expected_plan_sha256="not-a-digest",
        )
    with pytest.raises(ValueError, match="does not match the reviewed plan"):
        workspace.clone_ped_bundle(
            plan, expected_revision=0, expected_plan_sha256="0" * 64,
        )
    with pytest.raises(ValueError, match="reviewed plan object"):
        workspace.clone_ped_bundle(
            object(), expected_revision=0, expected_plan_sha256="0" * 64,
        )

    tampered = plan.to_dict()
    tampered["spec"]["updates"]["ped.unknown"] = "value"
    with pytest.raises(ValueError, match="Unsupported ped clone fields"):
        workspace.clone_ped_bundle(
            tampered, expected_revision=0,
            expected_plan_sha256=plan.plan_sha256,
        )

    blocked = workspace.plan_ped_clone("ig_author", ped_name="ig_missing")
    with pytest.raises(ValueError, match="plan is not ready"):
        workspace.clone_ped_bundle(
            blocked, expected_revision=0,
            expected_plan_sha256=blocked.plan_sha256,
        )
    assert workspace.revision == 0
    assert not list((workspace.root / "history").iterdir())


def test_ped_clone_digest_binds_asset_bytes_and_dict_plan_round_trips(tmp_path):
    workspace = _workspace(tmp_path)
    reviewed = workspace.plan_ped_clone("ig_author", ped_name="ig_clone")
    asset = workspace.source / "stream" / "ig_clone.ydd"
    asset.write_bytes(b"X" * len(asset.read_bytes()))
    with pytest.raises(ValueError, match="plan is stale"):
        workspace.clone_ped_bundle(
            reviewed.to_dict(), expected_revision=0,
            expected_plan_sha256=reviewed.plan_sha256,
        )

    current = workspace.plan_ped_clone("ig_author", ped_name="ig_clone")
    result = workspace.clone_ped_bundle(
        current.to_dict(), expected_revision=0,
        expected_plan_sha256=current.plan_sha256,
    )
    assert result.revision == 1
    assert workspace.values("ig_clone").ped == "ig_clone"


def test_ped_clone_verification_failure_restores_exact_workspace(
    tmp_path, monkeypatch,
):
    workspace = _workspace(tmp_path)
    before = (workspace.source / "peds.meta").read_bytes()
    plan = workspace.plan_ped_clone("ig_author", ped_name="ig_clone")

    def reject(*_args, **_kwargs):
        raise RuntimeError("forced clone verification failure")

    monkeypatch.setattr(workspace, "_verify_ped_clone", reject)
    with pytest.raises(RuntimeError, match="forced clone verification"):
        workspace.clone_ped_bundle(
            plan, expected_revision=0,
            expected_plan_sha256=plan.plan_sha256,
        )
    assert workspace.revision == 0
    assert (workspace.source / "peds.meta").read_bytes() == before
    assert workspace.manifest["created_records"] == []
    assert not list((workspace.root / "history").iterdir())


def test_ped_identity_migration_renames_assets_atomically_and_undoes(tmp_path):
    source = _ped_package(tmp_path)
    original_source = {
        item.name: item.read_bytes() for item in (source / "stream").iterdir()
    }
    workspace = PedAuthoringWorkspace.create(source, tmp_path / "workspace")
    result = workspace.migrate_identity(
        "ig_author", new_name="ig_migrated", expected_revision=0,
    )
    assert result.revision == 1
    assert workspace.values("ig_migrated").values["ped.propsName"] \
        == "ig_migrated_p"
    for name in (
        "ig_migrated.ydd", "ig_migrated.ytd",
        "ig_migrated_p.ydd", "ig_migrated_p.ytd",
    ):
        assert (workspace.source / "stream" / name).is_file()
    for name in (
        "ig_author.ydd", "ig_author.ytd",
        "ig_author_p.ydd", "ig_author_p.ytd",
    ):
        assert not (workspace.source / "stream" / name).exists()
    assert {
        item.name: item.read_bytes() for item in (source / "stream").iterdir()
    } == original_source

    workspace.undo(expected_revision=1)
    assert workspace.values("ig_author").values["ped.propsName"] == "ig_author_p"
    for name, data in original_source.items():
        assert (workspace.source / "stream" / name).read_bytes() == data


def test_ped_identity_migration_rejects_destination_collision(tmp_path):
    workspace = _workspace(tmp_path)
    with pytest.raises(ValueError, match="destination exists"):
        workspace.migrate_identity("ig_author", new_name="ig_clone")
    assert workspace.revision == 0


def test_ped_identity_migration_validates_noop_names_and_owned_assets(tmp_path):
    workspace = _workspace(tmp_path)
    with pytest.raises(ValueError, match="contains no changed values"):
        workspace.migrate_identity("ig_author", new_name="IG_AUTHOR")
    with pytest.raises(ValueError, match="safe game identifier"):
        workspace.migrate_identity("ig_author", new_name="../escape")
    with pytest.raises(ValueError, match="props name must be"):
        workspace.migrate_identity(
            "ig_author", new_name="ig_migrated", new_props="",
        )
    with pytest.raises(ValueError, match="non-negative integer"):
        workspace.migrate_identity(
            "ig_author", new_name="ig_migrated", expected_revision=True,
        )

    (workspace.source / "stream" / "ig_author.ytd").unlink()
    with pytest.raises(ValueError, match="one owned model texture"):
        workspace.migrate_identity("ig_author", new_name="ig_migrated")
    assert workspace.revision == 0


def test_ped_identity_can_migrate_props_only_and_preserve_model_assets(tmp_path):
    workspace = _workspace(tmp_path)
    model_before = {
        name: (workspace.source / "stream" / name).read_bytes()
        for name in ("ig_author.ydd", "ig_author.ytd")
    }
    result = workspace.migrate_identity(
        "ig_author", new_name="ig_author", new_props="ig_props_new",
        expected_revision=0,
    )
    assert result.revision == 1
    assert workspace.values("ig_author").values["ped.propsName"] == "ig_props_new"
    assert (workspace.source / "stream" / "ig_props_new.ydd").read_bytes() \
        == b"prop-drawable"
    assert (workspace.source / "stream" / "ig_props_new.ytd").read_bytes() \
        == b"prop-texture"
    for name, content in model_before.items():
        assert (workspace.source / "stream" / name).read_bytes() == content

    workspace.undo(expected_revision=1)
    assert workspace.values("ig_author").values["ped.propsName"] == "ig_author_p"
    assert (workspace.source / "stream" / "ig_author_p.ydd").is_file()


def test_ped_identity_keeps_nonconventional_shared_props_by_default(tmp_path):
    source = _ped_package(tmp_path)
    metadata = (source / "peds.meta").read_text(encoding="utf-8").replace(
        "<PropsName>ig_author_p</PropsName>",
        "<PropsName>shared_human_props</PropsName>",
    )
    (source / "peds.meta").write_text(metadata, encoding="utf-8")
    stream = source / "stream"
    (stream / "shared_human_props.ydd").write_bytes(b"shared-prop-drawable")
    (stream / "shared_human_props.ytd").write_bytes(b"shared-prop-texture")
    workspace = PedAuthoringWorkspace.create(source, tmp_path / "workspace")

    workspace.migrate_identity("ig_author", new_name="ig_migrated")
    assert workspace.values("ig_migrated").values["ped.propsName"] \
        == "shared_human_props"
    assert (workspace.source / "stream" / "shared_human_props.ydd").is_file()
    assert not (workspace.source / "stream" / "ig_migrated_p.ydd").exists()


def test_ped_identity_post_move_verification_failure_reverses_every_rename(
    tmp_path, monkeypatch,
):
    workspace = _workspace(tmp_path)
    before_meta = (workspace.source / "peds.meta").read_bytes()
    before_assets = {
        item.name: item.read_bytes()
        for item in (workspace.source / "stream").iterdir()
    }

    def reject(*_args, **_kwargs):
        raise RuntimeError("forced identity verification failure")

    monkeypatch.setattr(workspace, "_verify_identity_migration", reject)
    with pytest.raises(RuntimeError, match="forced identity verification"):
        workspace.migrate_identity(
            "ig_author", new_name="ig_migrated", expected_revision=0,
        )
    assert workspace.revision == 0
    assert (workspace.source / "peds.meta").read_bytes() == before_meta
    assert {
        item.name: item.read_bytes()
        for item in (workspace.source / "stream").iterdir()
    } == before_assets
    assert not list((workspace.root / "history").iterdir())


def test_failed_identity_undo_recovers_the_complete_post_edit_state(
    tmp_path, monkeypatch,
):
    workspace = _workspace(tmp_path)
    result = workspace.migrate_identity(
        "ig_author", new_name="ig_migrated", expected_revision=0,
    )

    def reject_manifest_write():
        raise OSError("forced manifest write failure")

    monkeypatch.setattr(workspace._core, "write_manifest", reject_manifest_write)
    with pytest.raises(OSError, match="forced manifest write failure"):
        workspace.undo(expected_revision=1)
    assert workspace.revision == 1
    assert workspace.values("ig_migrated").ped == "ig_migrated"
    assert (workspace.source / "stream" / "ig_migrated.ydd").is_file()
    assert not (workspace.source / "stream" / "ig_author.ydd").exists()
    assert result.history.is_dir()
    assert not list((workspace.root / "history").glob("*.undo-recovery"))


def test_ped_identity_rejects_existing_metadata_before_touching_assets(tmp_path):
    workspace = _workspace(tmp_path)
    plan = workspace.plan_ped_clone("ig_author", ped_name="ig_clone")
    workspace.clone_ped_bundle(
        plan, expected_revision=0, expected_plan_sha256=plan.plan_sha256,
    )
    before = (workspace.source / "peds.meta").read_bytes()
    with pytest.raises(ValueError, match="Ped identity already exists"):
        workspace.migrate_identity(
            "ig_author", new_name="ig_clone", expected_revision=1,
        )
    assert workspace.revision == 1
    assert (workspace.source / "peds.meta").read_bytes() == before


def test_ped_identity_migration_undo_rejects_asset_or_history_tampering(tmp_path):
    workspace = _workspace(tmp_path)
    result = workspace.migrate_identity(
        "ig_author", new_name="ig_migrated", expected_revision=0,
    )
    migrated = workspace.source / "stream" / "ig_migrated.ydd"
    migrated.write_bytes(b"external replacement")
    with pytest.raises(ValueError, match="changed after its edit"):
        workspace.undo(expected_revision=1)
    assert workspace.revision == 1

    # Restore the expected post-edit bytes, then prove an injected rename that
    # was never included in the snapshot cannot become restore authority.
    migrated.write_bytes(b"ped-drawable")
    record_path = result.history / "edit.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["renames"].append({
        "before": "stream/unrelated.ydd", "after": "stream/hijacked.ydd",
    })
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="conflicting renames"):
        workspace.undo(expected_revision=1)
    assert workspace.revision == 1


@pytest.mark.parametrize("renames", [
    "not-a-list",
    ["not-an-object"],
    [{"before": 1, "after": "stream/new.ydd"}],
    [{"before": "stream/ig_author.ydd", "after": "stream/ig_author.ydd"}],
    [{"before": "stream/not-snapshotted.ydd", "after": "stream/new.ydd"}],
    [{"before": "stream/ig_author.ydd", "after": "peds.meta"}],
    [
        {"before": "stream/ig_author.ydd", "after": "stream/new.ydd"},
        {"before": "stream/ig_author.ydd", "after": "stream/other.ydd"},
    ],
])
def test_rename_history_parser_fails_closed_for_untrusted_graphs(tmp_path, renames):
    workspace = _workspace(tmp_path)
    record = {
        "files": ["peds.meta", "stream/ig_author.ydd"],
        "renames": renames,
    }
    with pytest.raises(ValueError, match="invalid renames|invalid rename|conflicting"):
        workspace._core._history_renames(record)


def test_legacy_post_edit_hash_history_remains_undo_compatible(tmp_path):
    workspace = _workspace(tmp_path)
    result = workspace.update("ig_author", {"ped.pedType": "CIVMALE"})
    record_path = result.history / "edit.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["sha256_after"] = {
        path: descriptor["sha256"]
        for path, descriptor in record["sha256_after"].items()
    }
    record_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    undone = workspace.undo(expected_revision=1)
    assert undone.revision == 2
    assert workspace.values("ig_author").values["ped.pedType"] == "PERSON"


def test_ped_publish_source_requires_rebuildable_rpf_source_after_edits(tmp_path):
    workspace = _workspace(tmp_path)
    assert workspace.publish_source() == workspace.source
    workspace.update("ig_author", {"ped.pedType": "CIVMALE"})
    with pytest.raises(ValueError, match="prebuilt dlc.rpf"):
        workspace.publish_source()

    authored = workspace.source / "nested" / "dlc.rpf.source"
    authored.mkdir(parents=True)
    assert workspace.publish_source() == workspace.source
    (workspace.source / "second" / "dlc.rpf.source").mkdir(parents=True)
    with pytest.raises(ValueError, match="multiple dlc.rpf.source"):
        workspace.publish_source()


def test_ped_workspace_preserves_direct_dlc_source_publish_boundary(tmp_path):
    source = _ped_package(tmp_path)
    direct = source.with_name("dlc.rpf.source")
    source.rename(direct)
    workspace = PedAuthoringWorkspace.create(direct, tmp_path / "workspace")
    assert workspace.source.name == "dlc.rpf.source"
    workspace.update("ig_author", {"ped.pedType": "CIVMALE"})
    assert workspace.publish_source() == workspace.source


def test_ped_validation_regression_filter_ignores_info_and_blocks_new_warning(tmp_path):
    workspace = _workspace(tmp_path)
    before = workspace.inspect()
    informational = replace(
        before,
        findings=before.findings + (
            PackageFinding("info", "ped_note", "informational", "peds.meta"),
        ),
    )
    workspace._reject_validation_regressions(before, informational)
    warning = replace(
        before,
        findings=before.findings + (
            PackageFinding("warning", "ped_broken_link", "new warning", "peds.meta"),
        ),
    )
    with pytest.raises(ValueError, match="ped_broken_link: new warning"):
        workspace._reject_validation_regressions(before, warning)


def test_ped_authoring_cli_and_agent_api_share_guarded_transactions(tmp_path):
    runner = CliRunner()
    workspace = tmp_path / "ped-workspace"
    created = runner.invoke(main, [
        "sdk", "create-ped-authoring", str(_ped_package(tmp_path)),
        "--output-dir", str(workspace),
    ])
    assert created.exit_code == 0, created.output
    payload = json.loads(created.output)
    assert payload["revision"] == 0
    assert payload["peds"] == ["ig_author"]

    edited = runner.invoke(main, [
        "set-ped-fields", str(workspace), "ig_author",
        "--set", "ped.expressionSet=expr_set_author",
        "--expected-revision", "0", "--acknowledge-edit",
    ])
    assert edited.exit_code == 0, edited.output
    assert json.loads(edited.output)["revision"] == 1

    inspected = runner.invoke(main, [
        "inspect-ped-authoring", str(workspace), "--ped", "ig_author",
    ])
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.output)["ped_authoring"]["values"][
        "ped.expressionSet"
    ] == "expr_set_author"

    catalog = {item["name"]: item for item in command_catalog()}
    assert catalog["create-ped-authoring"]["risk"] == "authoring_write"
    assert catalog["inspect-ped-authoring"]["risk"] == "read_only"
    assert catalog["set-ped-fields"]["risk"] == "authoring_write"
    assert catalog["undo-ped-edit"]["risk"] == "authoring_write"
    assert catalog["plan-ped-clone"]["risk"] == "read_only"
    assert catalog["clone-ped-bundle"]["risk"] == "authoring_write"
    assert catalog["migrate-ped-identity"]["risk"] == "authoring_write"
    clone_parameters = {
        item["name"]: item for item in catalog["clone-ped-bundle"]["parameters"]
    }
    assert clone_parameters["expected_revision"]["required"] is True
    assert clone_parameters["plan_sha256"]["required"] is True
    assert clone_parameters["acknowledge_edit"]["is_flag"] is True
    migrate_parameters = {
        item["name"]: item
        for item in catalog["migrate-ped-identity"]["parameters"]
    }
    assert migrate_parameters["new_name"]["required"] is True
    assert migrate_parameters["new_props"]["required"] is False

    response = execute_request({
        "id": "undo-ped", "action": "execute", "command": "undo-ped-edit",
        "args": [
            str(workspace), "--expected-revision", "1", "--acknowledge-edit",
        ],
    }, audit_path=tmp_path / "agent-audit.jsonl")
    assert response["ok"] is True
    assert response["risk"] == "authoring_write"
    assert PedAuthoringWorkspace(workspace).values("ig_author").values[
        "ped.expressionSet"
    ] == "expr_set_ambient_male"


def test_ped_clone_and_identity_migration_cli(tmp_path):
    runner = CliRunner()
    workspace = tmp_path / "ped-workspace"
    created = runner.invoke(main, [
        "create-ped-authoring", str(_ped_package(tmp_path)),
        "--output-dir", str(workspace),
    ])
    assert created.exit_code == 0, created.output

    planned = runner.invoke(main, [
        "plan-ped-clone", str(workspace), "ig_author",
        "--ped-name", "ig_clone", "--set", "ped.pedType=CIVMALE",
    ])
    assert planned.exit_code == 0, planned.output
    plan = json.loads(planned.output)
    assert plan["ready"] is True

    cloned = runner.invoke(main, [
        "clone-ped-bundle", str(workspace), "ig_author",
        "--ped-name", "ig_clone", "--set", "ped.pedType=CIVMALE",
        "--expected-revision", "0", "--plan-sha256", plan["plan_sha256"],
        "--acknowledge-edit",
    ])
    assert cloned.exit_code == 0, cloned.output
    assert json.loads(cloned.output)["revision"] == 1

    migrated = runner.invoke(main, [
        "migrate-ped-identity", str(workspace), "ig_author",
        "--new-name", "ig_migrated", "--expected-revision", "1",
        "--acknowledge-edit",
    ])
    assert migrated.exit_code == 0, migrated.output
    assert json.loads(migrated.output)["ped"] == "ig_migrated"


def test_ped_agent_api_plans_and_applies_only_acknowledged_authoring(tmp_path):
    workspace = tmp_path / "ped-workspace"
    PedAuthoringWorkspace.create(_ped_package(tmp_path), workspace)
    audit = tmp_path / "agent-audit.jsonl"
    plan_response = execute_request({
        "id": "ped-plan", "action": "execute", "command": "plan-ped-clone",
        "args": [str(workspace), "ig_author", "--ped-name", "ig_clone"],
    }, audit_path=audit)
    assert plan_response["ok"] is True
    assert plan_response["risk"] == "read_only"
    plan = json.loads(plan_response["result"]["output"])
    assert plan["ready"] is True

    missing_ack = execute_request({
        "id": "ped-clone-no-ack", "action": "execute",
        "command": "clone-ped-bundle",
        "args": [
            str(workspace), "ig_author", "--ped-name", "ig_clone",
            "--expected-revision", "0", "--plan-sha256", plan["plan_sha256"],
        ],
    }, audit_path=audit)
    assert missing_ack["ok"] is False
    assert missing_ack["risk"] == "authoring_write"
    assert "--acknowledge-edit" in missing_ack["result"]["output"]

    cloned = execute_request({
        "id": "ped-clone", "action": "execute", "command": "clone-ped-bundle",
        "args": [
            str(workspace), "ig_author", "--ped-name", "ig_clone",
            "--expected-revision", "0", "--plan-sha256", plan["plan_sha256"],
            "--acknowledge-edit",
        ],
    }, audit_path=audit)
    assert cloned["ok"] is True
    assert cloned["risk"] == "authoring_write"
    assert PedAuthoringWorkspace(workspace).revision == 1

    audit_rows = [
        json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()
    ]
    assert [(row["command"], row["risk"], row["allowed"]) for row in audit_rows] == [
        ("plan-ped-clone", "read_only", True),
        ("clone-ped-bundle", "authoring_write", True),
        ("clone-ped-bundle", "authoring_write", True),
    ]


def test_ped_cli_rejects_duplicate_assignments_and_stale_clone_digest(tmp_path):
    runner = CliRunner()
    workspace = PedAuthoringWorkspace.create(
        _ped_package(tmp_path), tmp_path / "workspace",
    )
    duplicate = runner.invoke(main, [
        "plan-ped-clone", str(workspace.root), "ig_author",
        "--ped-name", "ig_clone",
        "--set", "ped.pedType=CIVMALE", "--set", "ped.pedType=PERSON",
    ])
    assert duplicate.exit_code != 0
    assert "Duplicate or empty ped clone field" in duplicate.output

    reviewed = workspace.plan_ped_clone("ig_author", ped_name="ig_clone")
    stale = runner.invoke(main, [
        "clone-ped-bundle", str(workspace.root), "ig_author",
        "--ped-name", "ig_clone", "--expected-revision", "0",
        "--plan-sha256", "0" * 64, "--acknowledge-edit",
    ])
    assert stale.exit_code != 0
    assert "digest mismatch" in stale.output
    assert workspace.revision == 0
    assert reviewed.ready
