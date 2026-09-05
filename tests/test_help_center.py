from allin1_sdk.help_topics import HELP_TOPICS, search_help_topics


def test_help_center_has_unique_keys_and_core_workflows():
    keys = [topic.key for topic in HELP_TOPICS]
    assert len(keys) == len(set(keys))
    assert {
        "getting-started", "packages", "package-recipes", "asset-viewer",
        "product-workspace",
        "model-material-workbench",
        "quick-import", "workbench", "vehicle-workbench", "weapon-workbench",
        "ped-workbench", "rpf-explorer", "recovery",
    } <= set(keys)


def test_product_workspace_help_explains_component_evidence_boundaries():
    topic = next(topic for topic in HELP_TOPICS if topic.key == "product-workspace")
    body = topic.body.casefold()

    assert "managed built-ins" in body
    assert "installable packages" in body
    assert "matched file/byte total" in body
    assert "unique file/byte total" in body
    assert "shared file/byte total" in body
    assert "unassigned evidence" in body
    assert "never imports or executes" in body
    assert "read-only agent api" in body


def test_navigation_help_matches_the_unified_shell_shortcuts():
    topic = next(topic for topic in HELP_TOPICS if topic.key == "input")

    assert "Content Workbench" in topic.body
    assert "Quick Import" in topic.body
    assert "Ctrl+I" in topic.body
    assert "Alt+Left" in topic.body
    assert "Ctrl+Tab" in topic.body
    assert "product header" in topic.body
    assert "white product header" not in topic.body
    assert "activity strip" in topic.body
    assert "Ctrl+O" in topic.body
    assert "F5" in topic.body


def test_workbench_help_explains_collapsible_side_panes():
    topic = next(topic for topic in HELP_TOPICS if topic.key == "workbench")

    assert "slim green divider arrows" in topic.body
    assert "restores its previous width, selection, and edit state" in topic.body


def test_quick_import_help_explains_launcher_owned_install_boundary():
    topic = next(topic for topic in HELP_TOPICS if topic.key == "quick-import")
    body = topic.body.casefold()

    assert "separate from the advanced content workbench" in body
    assert "traffic is off by default" in body
    assert "does not write gta v" in body
    assert "per-user allin1 package library" in body
    assert "export legacy oiv" in body
    assert "requires an explicit author" in body
    assert "gbay listings" in body and "intentionally not included" in body
    assert "enhanced-only packages" in body
    assert "weapon and ped quick import remain clearly marked" in body


def test_weapon_workbench_help_explains_guarded_authoring_boundaries():
    topic = next(topic for topic in HELP_TOPICS if topic.key == "weapon-workbench")
    body = topic.body.casefold()

    assert "create authoring workspace copies and verifies" in body
    assert "shared ammo or component definitions" in body
    assert "require a separate confirmation" in body
    assert "component type and attachment bone are visible but locked" in body
    assert "every exact animation-set and storefront source record" in body
    assert "only the weapon key changes" in body
    assert "ambiguous sources or partial mappings are rejected" in body
    assert "create complete weapon from donor" in body
    assert "unknown donor fields" in body
    assert "raw schema remain preserved and locked" in body
    assert "plan-weapon-clone is read-only" in body
    assert "exact --plan-sha256" in body
    assert "undo latest" in body
    assert "never write to the original package" in body


def test_ped_workbench_help_explains_guarded_authoring_boundaries():
    topic = next(topic for topic in HELP_TOPICS if topic.key == "ped-workbench")
    body = topic.body.casefold()

    assert "create authoring workspace copies and verifies" in body
    assert "normal field edits keep the ped identity locked" in body
    assert "without inventing missing schema nodes" in body
    assert "text/value/ref representation" in body
    assert "rolls back any validation regression" in body
    assert "new from template handles the new-record boundary" in body
    assert "requires one exact target drawable and texture dictionary" in body
    assert "plan-ped-clone is read-only" in body
    assert "exact --plan-sha256" in body
    assert "renames exact package-owned ydd/ydr/ytd/ymt" in body
    assert "undo latest" in body
    assert "never write to the original package" in body


def test_help_search_matches_keywords_and_ranks_title_matches():
    matches = search_help_topics("RPF")
    assert matches
    assert matches[0].key == "rpf-explorer"
    assert all("rpf" in " ".join((
        topic.title, topic.summary, topic.body, *topic.keywords,
    )).casefold() for topic in matches)


def test_help_search_requires_every_word_and_empty_query_returns_all():
    assert search_help_topics("") == HELP_TOPICS
    assert search_help_topics("command line")[0].key == "gameplay"
    assert search_help_topics("definitely-not-a-topic") == ()


def test_vehicle_workbench_help_covers_optional_compiled_render_workflow():
    topic = next(topic for topic in HELP_TOPICS if topic.key == "vehicle-workbench")
    body = topic.body.casefold()

    assert "live viewport" in body
    assert "works without blender" in body
    assert "studio / compiled render" in body
    assert "separately installed, optional offline renderer" in body
    assert "eevee or cycles render engine" in body
    assert "cpu/gpu device" in body
    assert "resolution" in body
    assert "studio lighting rig" in body
    assert "background" in body
    assert "cancel cooperatively stops" in body
    assert "open output" in body
    assert "output-only" in body
    assert "writes only the verified png" in body
    assert "never changes the source package" in body
    assert "gta v installation" in body
    assert "preserves uv0" in body
    assert "same-name sibling ytd automatically" in body
    assert "missing shared-game textures retain the semantic material fallback" in body
    assert "approximates game shader programs, reflections, and skinning" in body


def test_vehicle_workbench_help_search_finds_compiled_render_terms():
    assert search_help_topics("studio render")[0].key == "vehicle-workbench"
    assert search_help_topics("blender cycles")[0].key == "vehicle-workbench"
