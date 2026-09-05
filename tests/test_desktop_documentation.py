"""SDK-owned reference checks, runnable without a Launcher checkout."""
import json
from pathlib import Path

import click
import pytest

from allin1_sdk.cli import main
from scripts.package_release import _copy_authoring_resources, _REQUIRED_AUTHORING_RESOURCES, _ROOT_DOCUMENTATION

ROOT = Path(__file__).resolve().parents[1]


def test_command_reference_matches_every_command_and_parameter():
    text = (ROOT / "docs/cli-reference.md").read_text(encoding="utf-8")
    expected = []
    def cell(value): return str(value).replace("|", "\\|").replace("\n", " ")
    def visit(command, prefix):
        params = [" / ".join(getattr(p, "opts", []) + getattr(p, "secondary_opts", [])) or p.name for p in command.params]
        expected.append(f"| `{prefix}` | {cell(command.get_short_help_str(limit=200))} | {cell(', '.join(params))} |")
        if isinstance(command, click.Group):
            with click.Context(command) as context:
                for name in command.list_commands(context): visit(command.get_command(context, name), prefix + " " + name)
    visit(main, "allin1-sdk")
    assert [line for line in text.splitlines() if line.startswith("| `allin1-sdk")] == expected


def test_current_guides_are_required_resources_and_versions_are_explicit():
    from allin1_sdk import __version__
    required = {path.as_posix() for path in _REQUIRED_AUTHORING_RESOURCES}
    assert {"docs/sdk-guide.md", "docs/release-0.6.4.md", "docs/cli-reference.md", "docs/validation.md", "RELEASE_NOTES.md", "desktop/README.md"} <= required
    catalog = json.loads((ROOT / "docs/catalog.json").read_text())
    assert catalog["release"] == __version__
    assert catalog["product"] == "sdk"
    assert "unsigned prerelease" in (ROOT / "README.md").read_text().lower()
    assert "not a release-qualified stable build" in (ROOT / "README.md").read_text().lower()
    for name in catalog["documents"]: assert (ROOT / name).is_file(), name


def test_resource_staging_includes_root_docs_without_generated_runtime(tmp_path):
    source = tmp_path / "source"; target = tmp_path / "candidate"
    for relative in _REQUIRED_AUTHORING_RESOURCES:
        path = source / relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    _copy_authoring_resources(source, target)
    for relative in _ROOT_DOCUMENTATION:
        assert (target / relative).read_text() == "fixture"
    assert (target / "docs/sdk-guide.md").is_file()


def test_tauri_bundle_includes_every_staged_root_document():
    config = json.loads((ROOT / "desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    resources = config["bundle"]["resources"]
    for relative in _ROOT_DOCUMENTATION:
        name = relative.as_posix()
        assert resources.get("standalone-resources/" + name) == name, name


def test_full_release_milestone_requires_tkinter_removal():
    guide = (ROOT / "docs/release-0.6.4.md").read_text(encoding="utf-8")
    milestone = guide.split("## Mandatory 0.6.4 full-release milestone")[1].split("## Distribution decision")[0]
    for requirement in ("both", "Tkinter", "_tkinter", "91%", "80%", "unsigned manual", "signature verification"):
        assert requirement.lower() in milestone.lower()


def test_missing_release_guide_fails_before_resource_writes(tmp_path):
    source = tmp_path / "source"; target = tmp_path / "candidate"
    for relative in _REQUIRED_AUTHORING_RESOURCES:
        if relative == Path("docs/release-0.6.4.md"): continue
        path = source / relative; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    with pytest.raises(ValueError, match="release-0.6.4.md"):
        _copy_authoring_resources(source, target)
    assert not target.exists()
