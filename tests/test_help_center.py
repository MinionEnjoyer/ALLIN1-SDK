from allin1_sdk.help_center import HELP_TOPICS, search_help_topics


def test_help_center_has_unique_keys_and_core_workflows():
    keys = [topic.key for topic in HELP_TOPICS]
    assert len(keys) == len(set(keys))
    assert {
        "getting-started", "packages", "package-recipes", "asset-viewer",
        "workbench", "vehicle-workbench", "weapon-workbench",
        "ped-workbench", "rpf-explorer", "recovery",
    } <= set(keys)


def test_navigation_help_matches_the_unified_shell_shortcuts():
    topic = next(topic for topic in HELP_TOPICS if topic.key == "input")

    assert "Content Workbench" in topic.body
    assert "Alt+Left" in topic.body
    assert "Ctrl+Tab" in topic.body


def test_workbench_help_explains_collapsible_side_panes():
    topic = next(topic for topic in HELP_TOPICS if topic.key == "workbench")

    assert "slim green divider arrows" in topic.body
    assert "restores its previous width, selection, and edit state" in topic.body


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
