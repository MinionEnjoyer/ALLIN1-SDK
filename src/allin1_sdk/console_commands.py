"""Tk-free CLI completion and command execution helpers. Desktop writes use the reviewed agent protocol."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import click
from click.testing import CliRunner


BUILTINS = {
    "help": "Show every command or detailed help for one command.",
    "clear": "Clear console output, or use 'clear history' to forget commands.",
    "cls": "Clear console output (alias for clear).",
    "clear-history": "Permanently clear saved console command history.",
    "history": "Show recent commands, limit the list, or clear it.",
    "copy": "Copy all visible console output to the clipboard.",
    "pwd": "Show the active SDK workspace directory.",
    "shortcuts": "Show console keyboard shortcuts.",
    "exit": "Close the SDK Console.",
}

BUILTIN_SYNTAX = {
    "help": "help [command]",
    "clear": "clear [console|history]",
    "cls": "cls",
    "clear-history": "clear-history",
    "history": "history [count|clear]",
    "copy": "copy",
    "pwd": "pwd",
    "shortcuts": "shortcuts",
    "exit": "exit",
}

SHORTCUT_HELP = """Console shortcuts:
  Ctrl+L         Clear visible console output
  Ctrl+Shift+L   Clear saved command history
  Ctrl+Space     Accept the selected completion
  Tab            Accept the selected completion
  Up / Down      Move through matches or command history
  Ctrl+Up/Down   Move through command history
  Escape         Clear the command; press again to close/collapse
"""


@dataclass(frozen=True)
class ConsoleCommand:
    name: str
    syntax: str
    description: str


@dataclass(frozen=True)
class ConsoleSuggestion:
    replacement: str
    label: str
    description: str
    kind: str = "command"


@dataclass(frozen=True)
class ConsoleResult:
    output: str = ""
    exit_code: int = 0
    action: str = ""


@lru_cache(maxsize=1)
def _cli_group() -> click.Group:
    # Import lazily so opening the main SDK window does not eagerly construct
    # every command dependency.
    from allin1_sdk.cli import main

    return main


def split_command_line(command_line: str) -> list[str]:
    """Split a Windows-friendly command line while tolerating quoted paths."""
    try:
        values = shlex.split(command_line, posix=False)
    except ValueError:
        values = command_line.split()
    return [
        value[1:-1]
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}
        else value
        for value in values
    ]


@lru_cache(maxsize=1)
def command_catalog() -> tuple[ConsoleCommand, ...]:
    """Return CLI and console built-ins in stable display order."""
    group = _cli_group()
    context = click.Context(group, info_name="allin1-sdk")
    commands = [
        ConsoleCommand(name, _command_syntax(name, group.get_command(context, name)),
                       (group.get_command(context, name).get_short_help_str()  # type: ignore[union-attr]
                        if group.get_command(context, name) else ""))
        for name in group.list_commands(context)
    ]
    commands.extend(
        ConsoleCommand(name, BUILTIN_SYNTAX[name], help_text)
        for name, help_text in BUILTINS.items()
    )
    return tuple(sorted(commands, key=lambda item: item.name.casefold()))


def _command_syntax(name: str, command: click.Command | None) -> str:
    if command is None:
        return name
    parts = [name]
    for parameter in command.params:
        if isinstance(parameter, click.Argument):
            value = parameter.human_readable_name.upper()
            parts.append(f"<{value}>" if parameter.required else f"[{value}]")
    if any(isinstance(parameter, click.Option) for parameter in command.params):
        parts.append("[options]")
    return " ".join(parts)


def _resolve_command(tokens: list[str]) -> click.Command | None:
    current: click.Command = _cli_group()
    context = click.Context(current, info_name="allin1-sdk")
    for token in tokens:
        if not isinstance(current, click.Group):
            break
        child = current.get_command(context, token)
        if child is None:
            return None
        current = child
        context = click.Context(current, info_name=token, parent=context)
    return current


def _active_token(command_line: str) -> tuple[str, str]:
    if not command_line or command_line[-1].isspace():
        return command_line, ""
    split_at = max(command_line.rfind(" "), command_line.rfind("\t"))
    return command_line[:split_at + 1], command_line[split_at + 1:]


def _quote_path(value: str) -> str:
    return f'"{value}"' if any(character.isspace() for character in value) else value


def _path_suggestions(
    head: str, active: str, cwd: Path, *, limit: int = 24,
) -> list[ConsoleSuggestion]:
    raw = active.strip('"\'')
    expanded = os.path.expanduser(raw)
    candidate = Path(expanded) if expanded else Path(".")
    if not candidate.is_absolute():
        candidate = cwd / candidate
    directory = candidate if raw.endswith(("/", "\\")) else candidate.parent
    prefix = "" if raw.endswith(("/", "\\")) else candidate.name
    try:
        entries = sorted(directory.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold()))
    except OSError:
        return []
    results: list[ConsoleSuggestion] = []
    for item in entries:
        if prefix and not item.name.casefold().startswith(prefix.casefold()):
            continue
        # Emit an absolute completion. The packaged console may be opened by
        # another launcher whose process working directory is unrelated to the
        # SDK workspace, so a visually correct relative suggestion is unsafe.
        value = str(item.resolve())
        if item.is_dir():
            value += os.sep
        results.append(ConsoleSuggestion(
            head + _quote_path(value), value,
            "Directory" if item.is_dir() else "File", "path",
        ))
        if len(results) >= limit:
            break
    return results


def suggestions_for(
    command_line: str, *, cwd: Path, history: Iterable[str] = (), limit: int = 24,
) -> tuple[ConsoleSuggestion, ...]:
    """Return progressively filtered command, option, history, and path matches."""
    text = command_line.lstrip()
    tokens = split_command_line(text)
    head, active = _active_token(text)
    catalog = command_catalog()
    results: list[ConsoleSuggestion] = []

    first_incomplete = not text.strip() or (len(tokens) <= 1 and not text.endswith(" "))
    if first_incomplete:
        needle = active.casefold()
        for item in catalog:
            if item.name.casefold().startswith(needle):
                results.append(ConsoleSuggestion(
                    item.name + " ", item.syntax, item.description,
                ))
    else:
        completed_tokens = split_command_line(head.strip())
        command = _resolve_command(completed_tokens)
        if completed_tokens == ["help"]:
            for item in catalog:
                if not item.name.casefold().startswith(active.casefold()):
                    continue
                results.append(ConsoleSuggestion(
                    head + item.name, item.syntax, item.description, "command",
                ))
        if completed_tokens == ["clear"] and "history".startswith(active.casefold()):
            results.append(ConsoleSuggestion(
                head + "history", "clear history",
                "Permanently clear saved console command history.", "argument",
            ))
        if completed_tokens == ["history"] and "clear".startswith(active.casefold()):
            results.append(ConsoleSuggestion(
                head + "clear", "history clear",
                "Permanently clear saved console command history.", "argument",
            ))
        if command is not None:
            if isinstance(command, click.Group):
                context = click.Context(command, info_name=completed_tokens[-1])
                for name in command.list_commands(context):
                    if name.casefold().startswith(active.casefold()):
                        child = command.get_command(context, name)
                        results.append(ConsoleSuggestion(
                            head + name + " ", _command_syntax(name, child),
                            child.get_short_help_str() if child else "Subcommand",
                            "command",
                        ))
            option_needle = active.casefold()
            used = {token.partition("=")[0].casefold() for token in tokens[1:]}
            for parameter in command.params:
                if not isinstance(parameter, click.Option):
                    continue
                option = next((item for item in parameter.opts if item.startswith("--")), parameter.opts[0])
                if option.casefold() in used and not parameter.multiple:
                    continue
                if not option_needle or option.casefold().startswith(option_needle):
                    suffix = " " if parameter.is_flag else " "
                    results.append(ConsoleSuggestion(
                        head + option + suffix, option,
                        parameter.help or "Command option", "option",
                    ))
            path_expected = any(
                isinstance(parameter.type, click.Path)
                for parameter in command.params
                if isinstance(parameter, (click.Argument, click.Option))
            )
            path_like = (
                path_expected and (not active or not active.startswith("-"))
            ) or any(mark in active for mark in ("/", "\\", ".", "~"))
            if path_like:
                results.extend(_path_suggestions(head, active, cwd))

    normalized = text.casefold()
    for previous in reversed(tuple(history)):
        if previous.casefold().startswith(normalized) and previous.casefold() != normalized:
            results.insert(0, ConsoleSuggestion(previous, previous, "Command history", "history"))

    unique: list[ConsoleSuggestion] = []
    seen: set[str] = set()
    for item in results:
        key = item.replacement.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= limit:
            break
    return tuple(unique)


def _usage_error(syntax: str) -> ConsoleResult:
    return ConsoleResult(f"Usage: {syntax}\n", 2)


def _builtin_help(name: str) -> ConsoleResult | None:
    normalized = name.casefold()
    if normalized not in BUILTINS:
        return None
    return ConsoleResult(
        f"{BUILTIN_SYNTAX[normalized]}\n  {BUILTINS[normalized]}\n",
    )


def execute_console_command(
    command_line: str, history: Iterable[str] = (), *, cwd: Path | None = None,
) -> ConsoleResult:
    """Execute one in-process CLI command and capture its terminal output."""
    args = split_command_line(command_line.strip())
    if not args:
        return ConsoleResult()
    command = args[0].casefold()
    if command == "clear-history":
        return (
            ConsoleResult("Command history cleared.\n", action="clear_history")
            if len(args) == 1 else _usage_error(BUILTIN_SYNTAX[command])
        )
    if command == "clear":
        if len(args) == 2 and args[1].casefold() == "history":
            return ConsoleResult("Command history cleared.\n", action="clear_history")
        if len(args) > 2 or (len(args) == 2 and args[1].casefold() != "console"):
            return _usage_error(BUILTIN_SYNTAX[command])
        return ConsoleResult(action="clear")
    if command == "cls":
        return ConsoleResult(action="clear") if len(args) == 1 else _usage_error(
            BUILTIN_SYNTAX[command],
        )
    if command == "exit":
        return ConsoleResult(action="exit") if len(args) == 1 else _usage_error(
            BUILTIN_SYNTAX[command],
        )
    if command == "history":
        if len(args) == 2 and args[1].casefold() in {"clear", "delete", "reset"}:
            return ConsoleResult("Command history cleared.\n", action="clear_history")
        values = list(history)
        start = 0
        if len(args) > 2:
            return _usage_error(BUILTIN_SYNTAX[command])
        if len(args) == 2:
            try:
                count = int(args[1])
            except ValueError:
                return _usage_error(BUILTIN_SYNTAX[command])
            if not 1 <= count <= 200:
                return ConsoleResult("History count must be between 1 and 200.\n", 2)
            start = max(0, len(values) - count)
        lines = [
            f"{number:>3}  {value}"
            for number, value in enumerate(values[start:], start + 1)
        ]
        return ConsoleResult("\n".join(lines) + ("\n" if lines else "No command history.\n"))
    if command == "copy":
        return ConsoleResult(action="copy_output") if len(args) == 1 else _usage_error(
            BUILTIN_SYNTAX[command],
        )
    if command == "pwd":
        if len(args) != 1:
            return _usage_error(BUILTIN_SYNTAX[command])
        return ConsoleResult(str((cwd or Path.cwd()).resolve()) + "\n")
    if command == "shortcuts":
        return ConsoleResult(SHORTCUT_HELP) if len(args) == 1 else _usage_error(
            BUILTIN_SYNTAX[command],
        )
    if command == "help":
        if len(args) > 2:
            return _usage_error(BUILTIN_SYNTAX[command])
        if len(args) == 2:
            builtin = _builtin_help(args[1])
            if builtin is not None:
                return builtin
        args = ([args[1], "--help"] if len(args) > 1 else ["--help"])
    result = CliRunner().invoke(
        _cli_group(), args, color=False, prog_name="allin1-sdk",
    )
    output = result.output
    if result.exception and not isinstance(result.exception, SystemExit):
        detail = str(result.exception).strip()
        if detail and detail not in output:
            output += f"ERROR: {detail}\n"
    if command == "help" and len(args) == 1 and result.exit_code == 0:
        output += "\nConsole commands:\n"
        output += "\n".join(
            f"  {BUILTIN_SYNTAX[name]:<27} {description}"
            for name, description in BUILTINS.items()
        ) + "\n"
    return ConsoleResult(output, result.exit_code)
