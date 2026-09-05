from pathlib import Path

import pytest

from scripts.release_notes import prepare, render_notes

ROOT = Path(__file__).resolve().parents[1]


def notes(status=" — unreleased"):
    return f"""# ALLIN1 SDK 0.6.4{status}

## What's new

- Reviewed authoring workflows.

## Download and trust

**Unsigned manual download.** Verify SHA-256 checksums.
[Manual](docs/sdk-guide.md#workspaces)

## Release status

See the release qualification report.
"""


def test_current_notes_are_concise_and_tag_bound():
    from allin1_sdk import __version__
    text = (ROOT / "RELEASE_NOTES.md").read_text(encoding="utf-8")
    rendered = render_notes(text, __version__, "sdk")
    assert "/blob/v0.6.4/docs/sdk-guide.md" in rendered
    assert "What's new in 0.6.3" not in rendered
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert [line for line in readme.splitlines() if line.startswith("## What's new")] == [f"## What's new in {__version__}"]
    assert len(readme.splitlines()) <= 45
    assert len(readme.split()) <= 300
    history = (ROOT / "docs/archive/release-notes-before-0.6.4.md").read_text(encoding="utf-8")
    assert "What's new in 0.6.3" in history and "What's new in 0.5.4" in history


@pytest.mark.parametrize("case", ["version", "product", "old-release", "duplicate", "missing-trust", "long"])
def test_ambiguous_or_spammy_notes_are_refused(case):
    text = notes()
    if case == "version": text = text.replace("0.6.4", "0.6.3")
    elif case == "product": text = text.replace("ALLIN1 SDK", "GTA V ALLIN1")
    elif case == "old-release": text += "\n## What's new in 0.6.3\nold changes\n"
    elif case == "duplicate": text += "\n## What's new\nmore changes\n"
    elif case == "missing-trust": text = text.replace("**Unsigned manual download.**", "")
    else: text += " extra" * 451
    with pytest.raises(ValueError): render_notes(text, "0.6.4", "sdk")


@pytest.mark.parametrize("version", ["", "vv0.6.4", "0.6.4/../../main", "0.6.4\n", "0.6.4-rc.1"])
def test_ambiguous_tags_are_refused(version):
    with pytest.raises(ValueError): render_notes(notes(), version, "sdk")


@pytest.mark.parametrize("link", ["../secret.md", "/private.md", "C:\\secret.md", "%2e%2e/secret.md", "file:///private", "https://example.invalid/path"])
def test_release_links_are_safe_or_already_https(link):
    text = notes().replace("docs/sdk-guide.md#workspaces", link)
    if link.startswith("https://"):
        assert link in render_notes(text, "v0.6.4", "sdk")
    else:
        with pytest.raises(ValueError): render_notes(text, "0.6.4", "sdk")


def test_final_guard_does_not_accept_a_draft():
    with pytest.raises(ValueError, match="Unreleased/draft"):
        render_notes(notes(), "0.6.4", "sdk", require_final=True)
    assert render_notes(notes(""), "0.6.4", "sdk", require_final=True)


def test_prerelease_links_are_bound_to_the_candidate_tag_and_cannot_qualify_final():
    text = notes(" — unsigned prerelease")
    rendered = render_notes(text, "0.6.4", "sdk", release_ref="v0.6.4-rc.1")
    assert "/blob/v0.6.4-rc.1/docs/sdk-guide.md" in rendered
    with pytest.raises(ValueError):
        render_notes(text, "0.6.4", "sdk", release_ref="v0.6.4-rc.1", require_final=True)


@pytest.mark.parametrize("ref", ["main", "v0.6.5-rc.1", "v0.6.4-rc.0", "v0.6.4-rc.1/../main", "v0.6.4-rc.1\n"])
def test_prerelease_refs_cannot_escape_or_misidentify_the_version(ref):
    with pytest.raises(ValueError): render_notes(notes(), "0.6.4", "sdk", release_ref=ref)


def source(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="allin1-sdk"\nversion="0.6.4"\n', encoding="utf-8")
    (tmp_path / "RELEASE_NOTES.md").write_text(notes(), encoding="utf-8")


def test_render_is_new_file_only_and_failures_do_not_write(tmp_path):
    source(tmp_path)
    with pytest.raises(ValueError): prepare(tmp_path, "sdk", "0.6.3", "build/bad.md")
    with pytest.raises(ValueError): prepare(tmp_path, "sdk", "0.6.4", "build/bad.md", require_final=True)
    assert not (tmp_path / "build").exists()
    prepare(tmp_path, "sdk", "0.6.4", "build/notes.md")
    before = (tmp_path / "build/notes.md").read_bytes()
    with pytest.raises(FileExistsError): prepare(tmp_path, "sdk", "0.6.4", "build/notes.md")
    assert (tmp_path / "build/notes.md").read_bytes() == before


@pytest.mark.parametrize("output", ["RELEASE_NOTES.md", "../outside.md", "C:/outside.md", "build/../outside.md"])
def test_render_output_cannot_escape_build(tmp_path, output):
    source(tmp_path)
    before = (tmp_path / "RELEASE_NOTES.md").read_bytes()
    with pytest.raises(ValueError): prepare(tmp_path, "sdk", "0.6.4", output)
    assert (tmp_path / "RELEASE_NOTES.md").read_bytes() == before
    assert not (tmp_path / "build").exists()


def test_ci_cannot_automatically_publish_legacy_or_generated_notes():
    workflow = (ROOT / ".github/workflows/ci-release.yml").read_text(encoding="utf-8")
    assert "\n  release:\n" not in workflow
    assert "\n  manual-build:\n" not in workflow
    assert "--generate-notes" not in workflow
    assert "gh release create" not in workflow and "contents: write" not in workflow
    tauri = (ROOT / ".github/workflows/tauri-desktop.yml").read_text(encoding="utf-8")
    assert "contents: write" not in tauri and "gh release create" not in tauri
    assert "*-portable.zip" in tauri and "UNSIGNED_BUILD.txt" in tauri
    assert "scripts/release_notes.py" in tauri
