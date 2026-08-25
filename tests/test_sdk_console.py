import tkinter as tk
from pathlib import Path
from unittest.mock import Mock

import pytest

import allin1_sdk.sdk_console as sdk_console
from allin1_sdk.sdk_console import (
    ConsoleResult,
    SdkConsoleDialog,
    command_catalog,
    execute_console_command,
    split_command_line,
    suggestions_for,
)


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk display is unavailable: {exc}")
    root.withdraw()
    try:
        yield root
    finally:
        if root.winfo_exists():
            root.destroy()


def test_command_catalog_combines_cli_and_console_builtins():
    first = command_catalog()
    assert command_catalog() is first
    catalog = {item.name: item for item in first}
    assert "index-rpf" in catalog
    assert catalog["index-rpf"].syntax.startswith("index-rpf <ARCHIVE>")
    assert catalog["help"].syntax == "help [command]"
    assert "clear" in catalog
    assert catalog["clear"].syntax == "clear [console|history]"
    assert catalog["clear-history"].syntax == "clear-history"
    assert catalog["history"].syntax == "history [count|clear]"
    assert "cls" in catalog
    assert "copy" in catalog
    assert "pwd" in catalog
    assert "shortcuts" in catalog
    assert "create-rpf-graph" in catalog
    assert "import-rpf-graph" in catalog
    assert "plan-rpf-graph-origin" in catalog
    assert "reparent-rpf-graph-node" in catalog
    assert "create-rpf-program" in catalog
    assert "connect-rpf-program-nodes" in catalog
    assert "run-rpf-program" in catalog
    assert "list-rpf-program-templates" in catalog
    assert "defragment-rpf" in catalog
    assert "derive-rpf-plan" in catalog
    assert "inspect-native-asset" in catalog
    assert "inspect-rpf-native-entry" in catalog
    assert "analyze-package-graph" in catalog
    assert "inspect-package-graph-relations" in catalog
    assert "plan-weapon-clone" in catalog
    assert "clone-weapon-bundle" in catalog
    assert "plan-ped-clone" in catalog
    assert "clone-ped-bundle" in catalog
    assert "migrate-ped-identity" in catalog


def test_progressive_command_option_and_alias_suggestions(tmp_path):
    commands = suggestions_for("ins", cwd=tmp_path, limit=30)
    assert [item.replacement for item in commands] == [
            "inspect-binary-workspace ", "inspect-log ",
            "inspect-material-workspace ", "inspect-model-materials ",
            "inspect-native-asset ",
        "inspect-package-graph-relations ",
        "inspect-package-receipt ",
        "inspect-package-rpfs ",
        "inspect-ped-authoring ",
        "inspect-product-workspace ",
        "inspect-rpf ", "inspect-rpf-change-set ", "inspect-rpf-graph ",
        "inspect-rpf-native-entry ",
        "inspect-rpf-program ", "inspect-source ",
        "inspect-story-axle-runtimes ", "inspect-vehicle-authoring ",
        "inspect-vehicle-axles ",
        "inspect-vehicle-distribution ",
        "inspect-vehicle-project ", "inspect-vehicle-quick-import ",
        "inspect-vehicle-tuning ",
        "inspect-weapon-animation ", "inspect-weapon-authoring ",
        "inspect-weapon-shop ",
        "inspect-workbench ",
        "install-package ",
    ]

    options = suggestions_for("inspect-rpf --g", cwd=tmp_path)
    assert options[0].replacement == "inspect-rpf --gta-path "
    assert options[0].kind == "option"

    aliases = suggestions_for("sdk ind", cwd=tmp_path)
    assert aliases[0].replacement == "sdk index-rpf "

    weapon_aliases = suggestions_for("sdk plan-weapon-c", cwd=tmp_path)
    assert weapon_aliases[0].replacement == "sdk plan-weapon-clone "

    ped_aliases = suggestions_for("sdk plan-ped-c", cwd=tmp_path)
    assert ped_aliases[0].replacement == "sdk plan-ped-clone "

    help_matches = suggestions_for("help can", cwd=tmp_path)
    assert help_matches[0].replacement == "help canary-rpf-transaction"
    assert suggestions_for("clear h", cwd=tmp_path)[0].replacement == "clear history"
    assert suggestions_for("history c", cwd=tmp_path)[0].replacement == "history clear"


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

    recent = execute_console_command(
        "history 2", ("one", "two", "three"),
    )
    assert "1  one" not in recent.output
    assert "2  two" in recent.output
    assert "3  three" in recent.output

    assert execute_console_command("history clear").action == "clear_history"
    assert execute_console_command("clear history").action == "clear_history"
    assert execute_console_command("clear-history").action == "clear_history"
    assert execute_console_command("clear-history now").exit_code == 2
    assert execute_console_command("history 0").exit_code == 2
    assert execute_console_command("history nope").exit_code == 2
    assert "clear [console|history]" in execute_console_command("help clear").output
    assert "Console commands:" in execute_console_command("help").output
    workspace_help = execute_console_command("help inspect-product-workspace")
    assert workspace_help.exit_code == 0
    workspace_help_text = workspace_help.output.casefold()
    assert "each component's source coverage" in workspace_help_text
    assert "shared/unassigned evidence" in workspace_help_text
    assert "never executed" in workspace_help_text
    assert "Ctrl+L" in execute_console_command("shortcuts").output
    assert execute_console_command("copy").action == "copy_output"
    assert execute_console_command("pwd", cwd=Path(".")).output.strip() == str(
        Path(".").resolve(),
    )

    missing = execute_console_command("not-a-command")
    assert missing.exit_code != 0
    assert "No such command" in missing.output
    assert execute_console_command("clear").action == "clear"
    assert execute_console_command("clear console").action == "clear"
    assert execute_console_command("cls").action == "clear"
    assert execute_console_command("exit").action == "exit"


def test_command_line_split_handles_quoted_windows_paths():
    assert split_command_line('validate "C:\\Mods\\Example Mod\\addon.json"') == [
        "validate", "C:\\Mods\\Example Mod\\addon.json",
    ]


def test_collapsed_console_does_not_steal_focus_and_has_keyboard_completion(
    tmp_path, monkeypatch, tk_root,
):
    monkeypatch.setattr(sdk_console, "user_data_root", lambda: tmp_path / "state")
    focus_calls = []
    monkeypatch.setattr(
        tk.Entry, "focus_set", lambda widget: focus_calls.append(widget),
    )

    console = SdkConsoleDialog(
        tk_root, tmp_path, embedded=True, docked=True,
    )

    assert console.entry not in focus_calls
    assert console.entry.bind("<Control-space>")
    assert console.entry.bind("<Control-Shift-L>")
    assert console.suggestions.cget("yscrollcommand")
    assert console.suggestion_scroll.winfo_manager() == "pack"


def test_exit_recovers_entry_collapses_and_restores_previous_focus(
    tmp_path, monkeypatch, tk_root,
):
    monkeypatch.setattr(sdk_console, "user_data_root", lambda: tmp_path / "state")
    console = SdkConsoleDialog(
        tk_root, tmp_path, embedded=True, docked=True,
    )
    previous = Mock()
    previous.winfo_exists.return_value = True
    console._return_focus = previous
    console._set_expanded(True)
    console.running = True
    console.entry.configure(state="disabled")

    console._finish(ConsoleResult(action="exit"))
    tk_root.update_idletasks()

    assert console.running is False
    assert str(console.entry.cget("state")) == "normal"
    assert console.expanded is False
    previous.focus_set.assert_called_once_with()


def test_worker_exception_is_rendered_and_console_recovers(
    tmp_path, monkeypatch, tk_root,
):
    monkeypatch.setattr(sdk_console, "user_data_root", lambda: tmp_path / "state")
    monkeypatch.setattr(
        sdk_console, "execute_console_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    class ImmediateThread:
        def __init__(self, *, target, daemon):
            self.target = target
            self.daemon = daemon

        def start(self):
            self.target()

    monkeypatch.setattr(sdk_console.threading, "Thread", ImmediateThread)
    console = SdkConsoleDialog(
        tk_root, tmp_path, embedded=True, docked=True,
    )
    console.command.set("list")

    console._execute()
    tk_root.update()

    assert console.running is False
    assert str(console.entry.cget("state")) == "normal"
    assert "Console command failed unexpectedly: boom" in console.output.get("1.0", "end")


def test_finish_restores_command_state_when_output_rendering_fails(
    tmp_path, monkeypatch, tk_root,
):
    monkeypatch.setattr(sdk_console, "user_data_root", lambda: tmp_path / "state")
    console = SdkConsoleDialog(
        tk_root, tmp_path, embedded=True, docked=True,
    )
    console.running = True
    console.entry.configure(state="disabled")
    monkeypatch.setattr(
        console, "_append", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("render")),
    )

    with pytest.raises(RuntimeError, match="render"):
        console._finish(ConsoleResult(output="result"))

    assert console.running is False
    assert str(console.entry.cget("state")) == "normal"


def test_clear_history_action_removes_memory_and_persistent_file(
    tmp_path, monkeypatch, tk_root,
):
    monkeypatch.setattr(sdk_console, "user_data_root", lambda: tmp_path / "state")
    console = SdkConsoleDialog(
        tk_root, tmp_path, embedded=True, docked=True,
    )
    console.history = ["list", "validate addon.json", "clear history"]
    console._save_history()
    assert console.history_path.is_file()
    console.running = True
    console.entry.configure(state="disabled")

    console._finish(ConsoleResult("Command history cleared.\n", action="clear_history"))

    assert console.history == []
    assert console.history_index == 0
    assert not console.history_path.exists()
    assert console.running is False
    assert str(console.entry.cget("state")) == "normal"
    assert "Command history cleared." in console.output.get("1.0", "end")


def test_copy_output_action_places_visible_console_text_on_clipboard(
    tmp_path, monkeypatch, tk_root,
):
    monkeypatch.setattr(sdk_console, "user_data_root", lambda: tmp_path / "state")
    console = SdkConsoleDialog(
        tk_root, tmp_path, embedded=True, docked=True,
    )
    console._append("diagnostic evidence\n")
    console.running = True
    console.entry.configure(state="disabled")

    console._finish(ConsoleResult(action="copy_output"))
    tk_root.update()

    assert "diagnostic evidence" in tk_root.clipboard_get()
    assert "copied to the clipboard" in console.output.get("1.0", "end")
