from allin1_sdk.help_center import HELP_TOPICS, search_help_topics


def test_help_center_has_unique_keys_and_core_workflows():
    keys = [topic.key for topic in HELP_TOPICS]
    assert len(keys) == len(set(keys))
    assert {
        "getting-started", "packages", "package-recipes", "asset-viewer",
        "vehicle-workbench", "rpf-explorer", "recovery",
    } <= set(keys)


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
