from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from click.testing import CliRunner

from allin1_sdk.addon_sdk import (
    AddonInstallStep,
    AddonLinker,
    AddonManifest,
    AddonNode,
    AddonReference,
    AddonSdkCatalog,
    field_description,
    hud_frame_label,
    joaat,
    signed_hash,
    summarize_values,
)
from allin1_sdk.cli import main


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "sdk" / "examples" / "colored_smokes" / "addon.json"


def _example_data() -> dict:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _write_manifest(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "addon.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _minimal_manifest(tmp_path: Path) -> AddonManifest:
    source = tmp_path / "source.txt"
    source.write_text("test", encoding="utf-8")
    data = {
        "schema_version": 1,
        "id": "test.addon",
        "name": "Test Add-on",
        "version": "1.0",
        "editions": ["legacy", "enhanced"],
        "nodes": [{
            "id": "package.main",
            "kind": "package",
            "source": "source.txt",
            "fields": {
                "Registration": "none",
                "Edition": "both",
                "Safety": "read-only",
            },
        }],
        "references": [],
        "install_steps": [{
            "id": "step.main", "order": 1, "title": "Inspect",
            "target": "none", "strategy": "read-only", "source": "source.txt",
        }],
    }
    return AddonManifest.load(_write_manifest(tmp_path, data))


def test_rockstar_hash_and_hud_frame_helpers_match_verified_smoke_hashes():
    assert joaat("weapon_allin1_smoke_white") == 0xF8C504D6
    assert signed_hash("WEAPON_ALLIN1_SMOKE_WHITE") == -121305898
    assert hud_frame_label("WEAPON_ALLIN1_SMOKE_RED") == "INT1261938036"
    assert field_description("AmmoInfo").startswith("Reference to the ammo")
    assert field_description("custom") == "Manifest-defined integration field."
    assert summarize_values(["red", "blue"]) == "red, blue"


def test_built_in_colored_smoke_example_links_every_integration_stage():
    catalog = AddonSdkCatalog(ROOT)
    manifests = catalog.discover()
    assert [item.addon_id for item in manifests] == ["allin1.colored_smokes"]
    report = AddonLinker().link(manifests[0])
    assert report.valid
    assert report.error_count == 0
    assert report.warning_count == 0
    assert len(report.manifest.nodes) == 12
    assert len(report.references) == 6
    assert all(item.valid for item in report.references)
    markdown = report.to_markdown()
    assert "Result: **PASS**" in markdown
    assert "Link BZ Gas wheel artwork" in markdown
    assert "6/6 resolved" in markdown


def test_catalog_is_empty_when_sdk_examples_directory_is_absent(tmp_path):
    assert AddonSdkCatalog(tmp_path).discover() == []


def test_catalog_discovers_installed_receipts_without_copying_payloads(tmp_path):
    package = tmp_path / "external-package"
    package.mkdir()
    (package / "plugin.dll").write_bytes(b"managed script")
    (package / "mod.toml").write_text(
        "schema_version = 1\n"
        'id = "test.installed-script"\n'
        'name = "Installed Script"\n'
        'version = "2.0"\n'
        'type = "script"\n'
        'description = "Receipt-backed SDK package"\n'
        'editions = ["legacy", "enhanced"]\n'
        'dependencies = ["shvdn"]\n'
        "[[files]]\n"
        'source = "plugin.dll"\n'
        'destination = "scripts/InstalledScript.dll"\n',
        encoding="utf-8",
    )
    game = tmp_path / "game"
    receipts = game / "scripts" / ".allin1" / "mods"
    receipts.mkdir(parents=True)
    (game / "GTA5_Enhanced.exe").write_bytes(b"exe")
    receipt_path = receipts / "test.installed-script.json"
    receipt_path.write_text(json.dumps({
        "id": "test.installed-script",
        "name": "Installed Script",
        "version": "2.0",
        "type": "script",
        "enabled": True,
        "source_manifest": str(package / "mod.toml"),
        "dependencies": ["shvdn"],
        "dlc_packs": [],
        "files": [{"destination": "scripts/InstalledScript.dll", "backup": None}],
        "rpf_entries": [],
    }), encoding="utf-8")

    catalog = AddonSdkCatalog(tmp_path)
    manifests = catalog.discover((game,), include_external=True)

    assert [item.addon_id for item in manifests] == ["test.installed-script"]
    manifest = manifests[0]
    assert manifest.catalog_state == "Installed · Enhanced"
    assert manifest.catalog_origin == "installed-receipt"
    assert manifest.package_source == package
    assert {node.kind for node in manifest.nodes} == {"package", "script_plugin"}
    assert AddonLinker().link(manifest).valid
    assert not (tmp_path / "mods" / "catalog").exists()


def test_catalog_reconstructs_receipt_when_original_manifest_is_gone(tmp_path):
    game = tmp_path / "legacy"
    receipts = game / "scripts" / ".allin1" / "mods"
    receipts.mkdir(parents=True)
    (game / "GTA5.exe").write_bytes(b"exe")
    receipt = receipts / "orphaned.package.json"
    receipt.write_text(json.dumps({
        "id": "orphaned.package", "name": "Orphaned Package",
        "version": "1", "type": "asi",
        "files": [{"destination": "Orphaned.asi"}],
        "rpf_entries": [{
            "archive": "mods/x64h.rpf", "entry": "levels/test.ymap",
        }],
        "dlc_packs": ["orphaned_pack"],
        "source_manifest": str(tmp_path / "missing" / "mod.toml"),
    }), encoding="utf-8")

    manifest = AddonSdkCatalog(tmp_path).discover(
        (game,), include_external=True,
    )[0]

    assert manifest.addon_id == "orphaned.package"
    assert manifest.editions == ("legacy",)
    assert manifest.manifest_path == receipt
    assert len(manifest.install_steps) == 2
    assert manifest.nodes[0].fields["Registration"] == ["orphaned_pack"]
    assert AddonLinker().link(manifest).valid


def test_imported_sdk_registry_persists_reference_and_deduplicates(tmp_path):
    manifest = _minimal_manifest(tmp_path)
    project = tmp_path / "project"
    catalog = AddonSdkCatalog(project)

    remembered = catalog.remember(
        manifest.manifest_path, source_root=tmp_path, package_source=tmp_path,
    )
    catalog.remember(
        manifest.manifest_path, source_root=tmp_path, package_source=tmp_path,
    )
    discovered = catalog.discover(include_external=True)

    assert remembered.catalog_state == "Imported draft"
    assert [item.addon_id for item in discovered] == ["test.addon"]
    registry = json.loads(catalog.registry_path.read_text(encoding="utf-8"))
    assert len(registry["manifests"]) == 1
    assert Path(registry["manifests"][0]["manifest"]) == manifest.manifest_path


@pytest.mark.parametrize("registry", [
    "{",
    json.dumps([]),
    json.dumps({"schema_version": 2, "manifests": []}),
    json.dumps({"schema_version": 1, "manifests": {}}),
])
def test_catalog_ignores_malformed_import_registry(tmp_path, registry):
    catalog = AddonSdkCatalog(tmp_path)
    catalog.registry_path.parent.mkdir(parents=True)
    catalog.registry_path.write_text(registry, encoding="utf-8")
    assert catalog.discover(include_external=True) == []


def test_catalog_builds_complete_graph_for_local_mixed_package(tmp_path):
    catalog_package = tmp_path / "mods" / "catalog" / "mixed-package"
    catalog_package.mkdir(parents=True)
    (catalog_package / "mod.toml").write_text(
        "schema_version = 1\n"
        'id = "test.mixed-package"\n'
        'name = "Mixed Package"\n'
        'version = "1.0"\n'
        'type = "mixed"\n'
        'editions = ["enhanced"]\n'
        'dependencies = ["openrpf", "shvdn"]\n'
        'dlc_packs = ["mixed_pack"]\n'
        "[[files]]\n"
        'source = "script.dll"\n'
        'destination = "scripts/Mixed/script.dll"\n'
        "[[files]]\n"
        'source = "native.asi"\n'
        'destination = "native.asi"\n'
        "[[files]]\n"
        'source = "dlc.rpf"\n'
        'destination = "mods/update/x64/dlcpacks/mixed_pack/dlc.rpf"\n'
        "[[rpf_entries]]\n"
        'source = "entry.ymap"\n'
        'archive = "mods/x64h.rpf"\n'
        'entry = "levels/test.ymap"\n',
        encoding="utf-8",
    )

    manifests = AddonSdkCatalog(tmp_path).discover(include_external=True)

    assert len(manifests) == 1
    manifest = manifests[0]
    assert manifest.catalog_state == "Available package"
    assert {node.kind for node in manifest.nodes} == {
        "package", "script_plugin", "asi_plugin", "replacement",
        "dlc_registration",
    }
    assert len(manifest.install_steps) == 4
    assert AddonLinker().link(manifest).valid


def test_catalog_skips_broken_external_catalog_and_receipt_records(tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    (external / "addon.json").write_text("{}", encoding="utf-8")
    catalog = AddonSdkCatalog(tmp_path)
    catalog.registry_path.parent.mkdir(parents=True, exist_ok=True)
    catalog.registry_path.write_text(json.dumps({
        "schema_version": 1,
        "manifests": [{"manifest": str(external / "addon.json")}],
    }), encoding="utf-8")
    local = tmp_path / "mods" / "catalog" / "broken"
    local.mkdir(parents=True)
    (local / "mod.toml").write_text("invalid", encoding="utf-8")
    game = tmp_path / "game"
    receipts = game / "scripts" / ".allin1" / "mods"
    receipts.mkdir(parents=True)
    (receipts / "broken.json").write_text("{", encoding="utf-8")

    assert catalog.discover((game,), include_external=True) == []


def test_manifest_directory_loading_and_defaults(tmp_path):
    manifest = _minimal_manifest(tmp_path)
    loaded = AddonManifest.load(tmp_path)
    assert loaded.addon_id == manifest.addon_id
    assert loaded.summary == ""
    assert loaded.nodes[0].label == "package.main"
    assert loaded.nodes[0].description == ""
    assert loaded.install_steps[0].description == ""
    assert loaded.node_map["package.main"].kind == "package"
    assert AddonLinker().link(loaded).valid


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda data: data.update(schema_version=2), "schema_version"),
        (lambda data: data.update(id="BAD ID"), "Add-on id"),
        (lambda data: data.update(name=""), "name and version"),
        (lambda data: data.update(editions=[]), "editions"),
        (lambda data: data.update(editions=["online"]), "editions"),
        (lambda data: data.update(nodes=[]), "at least one node"),
        (lambda data: data["nodes"][0].update(kind="mystery"), "Unsupported node kind"),
        (lambda data: data["nodes"][0].update(fields=[]), "fields must be an object"),
        (lambda data: data["nodes"][0].update(source=5), "source must be"),
        (lambda data: data["nodes"][0].update(source="../escape"), "escapes the SDK root"),
    ],
)
def test_manifest_rejects_invalid_core_shapes(tmp_path, mutation, message):
    data = _example_data()
    mutation(data)
    with pytest.raises(ValueError, match=message):
        AddonManifest.load(_write_manifest(tmp_path, data), source_root=ROOT)


def test_manifest_rejects_invalid_json_and_missing_file(tmp_path):
    path = tmp_path / "addon.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid SDK JSON"):
        AddonManifest.load(path)
    with pytest.raises(FileNotFoundError, match="SDK manifest not found"):
        AddonManifest.load(tmp_path / "missing.json")


def test_manifest_rejects_duplicate_and_malformed_nodes(tmp_path):
    data = _example_data()
    data["nodes"].append(dict(data["nodes"][0]))
    with pytest.raises(ValueError, match="Duplicate node id"):
        AddonManifest.load(_write_manifest(tmp_path, data), source_root=ROOT)

    data = _example_data()
    data["nodes"][0] = "bad"
    with pytest.raises(ValueError, match=r"nodes\[1\] must be an object"):
        AddonManifest.load(_write_manifest(tmp_path, data), source_root=ROOT)


def test_manifest_rejects_duplicate_and_malformed_references(tmp_path):
    data = _example_data()
    data["references"].append(dict(data["references"][0]))
    with pytest.raises(ValueError, match="Duplicate reference id"):
        AddonManifest.load(_write_manifest(tmp_path, data), source_root=ROOT)

    data = _example_data()
    data["references"][0] = "bad"
    with pytest.raises(ValueError, match=r"references\[1\] must be an object"):
        AddonManifest.load(_write_manifest(tmp_path, data), source_root=ROOT)


def test_manifest_rejects_duplicate_and_malformed_install_steps(tmp_path):
    data = _example_data()
    data["install_steps"].append(dict(data["install_steps"][0]))
    with pytest.raises(ValueError, match="Duplicate install step id"):
        AddonManifest.load(_write_manifest(tmp_path, data), source_root=ROOT)

    data = _example_data()
    data["install_steps"][0] = "bad"
    with pytest.raises(ValueError, match=r"install_steps\[1\] must be an object"):
        AddonManifest.load(_write_manifest(tmp_path, data), source_root=ROOT)

    data = _example_data()
    data["install_steps"][0]["source"] = 3
    with pytest.raises(ValueError, match="source must be a path"):
        AddonManifest.load(_write_manifest(tmp_path, data), source_root=ROOT)


def test_linker_reports_missing_fields_sources_and_bad_hud_hashes(tmp_path):
    manifest = _minimal_manifest(tmp_path)
    broken_package = replace(
        manifest.nodes[0], source="missing.txt", fields={"Registration": "none"}
    )
    hud = AddonNode(
        "hud.bad", "hud_alias", "Bad HUD", "", None,
        {
            "SourceWeaponNames": ["WEAPON_TEST"],
            "FrameTemplate": "INT-1600701090",
            "Archive": "hud.rpf", "Entry": "hud.gfx",
            "ExpectedFrames": {"WEAPON_TEST": "INT0"},
        },
    )
    bad = replace(manifest, nodes=(broken_package, hud))
    report = AddonLinker().link(bad)
    codes = {issue.code for issue in report.issues}
    assert not report.valid
    assert {"missing_field", "missing_source", "hud_hash_mismatch"}.issubset(codes)

    invalid_hud = replace(hud, fields={**hud.fields, "SourceWeaponNames": "bad"})
    report = AddonLinker().link(replace(manifest, nodes=(invalid_hud,)))
    assert "invalid_hud_alias" in {issue.code for issue in report.issues}


def test_linker_diagnoses_every_reference_failure_mode(tmp_path):
    manifest = _minimal_manifest(tmp_path)
    source = AddonNode(
        "source.node", "package", "Source", "", None,
        {"Registration": "alpha", "Edition": "both", "Safety": "safe"},
    )
    target = AddonNode(
        "target.node", "package", "Target", "", None,
        {"Registration": "beta", "Edition": "both", "Safety": "safe"},
    )
    references = (
        AddonReference("ref.missingnode", "missing.node", "x", "target.node", "Registration", "test", ""),
        AddonReference("ref.missingsource", "source.node", "missing", "target.node", "Registration", "test", ""),
        AddonReference("ref.missingtarget", "source.node", "Registration", "target.node", "missing", "test", ""),
        AddonReference("ref.mismatch", "source.node", "Registration", "target.node", "Registration", "test", ""),
        AddonReference("ref.optional", "missing.node", "x", "target.node", "Registration", "test", "", False),
    )
    report = AddonLinker().link(replace(
        manifest, nodes=(source, target), references=references,
    ))
    codes = {issue.code for issue in report.issues}
    assert {"missing_node", "missing_source_field", "missing_target_field", "reference_mismatch"} <= codes
    assert len(report.references) == 5
    assert not report.references[-1].valid
    assert report.error_count == 4
    assert "Result: **FAIL**" in report.to_markdown()


def test_linker_handles_scalar_list_and_mapping_targets(tmp_path):
    manifest = _minimal_manifest(tmp_path)
    source = replace(manifest.nodes[0], fields={
        "Registration": ["a", "b"], "Edition": "enhanced", "Safety": "safe"
    })
    target = AddonNode(
        "package.target", "package", "Target", "", None,
        {"Registration": ["b", "a"], "Edition": ["legacy", "enhanced"],
         "Safety": {"safe": True}},
    )
    refs = (
        AddonReference("ref.list", source.node_id, "Registration", target.node_id, "Registration", "test", ""),
        AddonReference("ref.member", source.node_id, "Edition", target.node_id, "Edition", "test", ""),
        AddonReference("ref.mapping", source.node_id, "Safety", target.node_id, "Safety", "test", ""),
    )
    report = AddonLinker().link(replace(manifest, nodes=(source, target), references=refs))
    assert report.valid
    assert all(item.valid for item in report.references)


def test_linker_requires_complete_weapon_links_and_valid_steps(tmp_path):
    manifest = _minimal_manifest(tmp_path)
    weapon = AddonNode(
        "weapon.test", "weapon", "Weapon", "", None,
        {"Name": "WEAPON_TEST", "Slot": "SLOT_TEST", "AmmoInfo": "AMMO_TEST",
         "Model": "model", "HumanNameHash": "WT_TEST", "StatName": "TEST"},
    )
    steps = (
        AddonInstallStep("step.one", 1, "One", "", "", None, ""),
        AddonInstallStep("step.two", 1, "Two", "target", "merge", "missing.txt", ""),
    )
    report = AddonLinker().link(replace(
        manifest, nodes=(weapon,), references=(), install_steps=steps,
    ))
    codes = {issue.code for issue in report.issues}
    assert "incomplete_weapon_integration" in codes
    assert "duplicate_step_order" in codes
    assert "incomplete_install_step" in codes
    assert "missing_step_source" in codes
    assert report.warning_count == 1


def test_sdk_cli_lists_validates_and_exports_link_report(tmp_path):
    runner = CliRunner()
    listed = runner.invoke(main, ["sdk", "list"])
    assert listed.exit_code == 0
    assert "allin1.colored_smokes" in listed.output

    validated = runner.invoke(main, ["sdk", "validate", str(EXAMPLE)])
    assert validated.exit_code == 0
    assert "PASS" in validated.output
    assert "6/6 references" in validated.output

    output = tmp_path / "linked.md"
    linked = runner.invoke(main, ["sdk", "link", str(EXAMPLE), "-o", str(output)])
    assert linked.exit_code == 0
    assert output.is_file()
    assert "colored smoke" in output.read_text(encoding="utf-8").lower()


def test_sdk_cli_returns_failure_for_a_broken_manifest(tmp_path):
    data = _example_data()
    data["nodes"][0]["fields"]["AmmoInfo"][0] = "AMMO_DOES_NOT_EXIST"
    path = _write_manifest(tmp_path, data)
    runner = CliRunner()
    result = runner.invoke(main, ["sdk", "validate", str(path)])
    assert result.exit_code == 1
    assert "reference_mismatch" in result.output
