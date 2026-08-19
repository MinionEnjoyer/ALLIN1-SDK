from pathlib import Path

from allin1_sdk.sdk_console import (
    command_catalog,
    execute_console_command,
    split_command_line,
    suggestions_for,
)


def test_command_catalog_combines_cli_and_console_builtins():
    catalog = {item.name: item for item in command_catalog()}
    assert "index-rpf" in catalog
    assert catalog["index-rpf"].syntax.startswith("index-rpf <ARCHIVE>")
    assert catalog["help"].syntax == "help [command]"
    assert "clear" in catalog
    assert "create-rpf-graph" in catalog
    assert "import-rpf-graph" in catalog
    assert "plan-rpf-graph-origin" in catalog
    assert "reparent-rpf-graph-node" in catalog
    assert "create-rpf-program" in catalog
    assert "connect-rpf-program-nodes" in catalog
    assert "run-rpf-program" in catalog
    assert "defragment-rpf" in catalog


def test_progressive_command_option_and_alias_suggestions(tmp_path):
    commands = suggestions_for("ins", cwd=tmp_path)
    assert [item.replacement for item in commands] == [
        "inspect-binary-workspace ", "inspect-package-rpfs ",
        "inspect-rpf ", "inspect-rpf-graph ", "inspect-rpf-program ",
        "install-package ",
    ]

    options = suggestions_for("inspect-rpf --g", cwd=tmp_path)
    assert options[0].replacement == "inspect-rpf --gta-path "
    assert options[0].kind == "option"

    aliases = suggestions_for("sdk ind", cwd=tmp_path)
    assert aliases[0].replacement == "sdk index-rpf "

    help_matches = suggestions_for("help can", cwd=tmp_path)
    assert help_matches[0].replacement == "help canary-rpf-transaction"


def test_path_and_history_suggestions_preserve_executable_command_text(tmp_path):
    (tmp_path / "Example Mod").mkdir()
    (tmp_path / "example.meta").write_text("<root />", encoding="utf-8")
    paths = suggestions_for("validate ex", cwd=tmp_path)
    replacements = {item.replacement for item in paths}
    assert f'validate "{tmp_path}\\Example Mod\\"' in replacements
    assert f"validate {tmp_path}\\example.meta" in replacements

    history = suggestions_for(
        "val", cwd=tmp_path, history=("validate example.meta",),
    )
    assert history[0].replacement == "validate example.meta"
    assert history[0].kind == "history"


def test_console_execution_help_history_and_errors():
    help_result = execute_console_command("help list")
    assert help_result.exit_code == 0
    assert "Usage: allin1-sdk list" in help_result.output

    history_result = execute_console_command(
        "history", ("list", "validate addon.json"),
    )
    assert "1  list" in history_result.output
    assert "2  validate addon.json" in history_result.output

    missing = execute_console_command("not-a-command")
    assert missing.exit_code != 0
    assert "No such command" in missing.output
    assert execute_console_command("clear").action == "clear"
    assert execute_console_command("exit").action == "exit"


def test_command_line_split_handles_quoted_windows_paths():
    assert split_command_line('validate "C:\\Mods\\Example Mod\\addon.json"') == [
        "validate", "C:\\Mods\\Example Mod\\addon.json",
    ]
