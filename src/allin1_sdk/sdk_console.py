"""Source-style in-app console for the standalone SDK command surface."""

from __future__ import annotations

import os
import shlex
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from typing import Iterable

import click
from click.testing import CliRunner

from allin1_sdk.paths import user_data_root


BUILTINS = {
    "help": "Show every command or detailed help for one command.",
    "clear": "Clear console output.",
    "history": "Show commands used in this console.",
    "exit": "Close the SDK Console.",
}


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
        ConsoleCommand(name, name + (" [command]" if name == "help" else ""), help_text)
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
    command_line: str, *, cwd: Path, history: Iterable[str] = (), limit: int = 12,
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
                if item.name in BUILTINS or not item.name.casefold().startswith(active.casefold()):
                    continue
                results.append(ConsoleSuggestion(
                    head + item.name, item.syntax, item.description, "command",
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


def execute_console_command(command_line: str, history: Iterable[str] = ()) -> ConsoleResult:
    """Execute one in-process CLI command and capture its terminal output."""
    args = split_command_line(command_line.strip())
    if not args:
        return ConsoleResult()
    command = args[0].casefold()
    if command == "clear":
        return ConsoleResult(action="clear")
    if command == "exit":
        return ConsoleResult(action="exit")
    if command == "history":
        lines = [f"{number:>3}  {value}" for number, value in enumerate(history, 1)]
        return ConsoleResult("\n".join(lines) + ("\n" if lines else "No command history.\n"))
    if command == "help":
        args = ([args[1], "--help"] if len(args) > 1 else ["--help"])
    result = CliRunner().invoke(
        _cli_group(), args, color=False, prog_name="allin1-sdk",
    )
    output = result.output
    if result.exception and not isinstance(result.exception, SystemExit):
        detail = str(result.exception).strip()
        if detail and detail not in output:
            output += f"ERROR: {detail}\n"
    return ConsoleResult(output, result.exit_code)


class SdkConsoleDialog(ttk.Frame):
    """Interactive SDK CLI embedded in the primary developer workspace."""

    def __init__(
        self, parent: tk.Misc, project_root: str | Path,
        *, embedded: bool = False, docked: bool = False, on_close=None,
    ) -> None:
        self._window: tk.Toplevel | None = None
        self._on_close = on_close
        self.docked = docked
        self.expanded = not docked
        host = parent
        if not embedded:
            self._window = tk.Toplevel(parent)
            self._window.title("ALLIN1 SDK Console")
            self._window.geometry("1040x680")
            self._window.minsize(760, 500)
            self._window.transient(parent.winfo_toplevel())
            host = self._window
        super().__init__(host)
        self.pack(fill="both", expand=True)
        self.project_root = Path(project_root).resolve()
        self.history_path = user_data_root() / "console-history.txt"
        self.history = self._load_history()
        self.history_index = len(self.history)
        self.matches: tuple[ConsoleSuggestion, ...] = ()
        self.running = False
        self._build()
        self._append(
            "ALLIN1 SDK Console\n"
            "Type a command. Tab accepts the selected completion; Up/Down navigates "
            "matches or history. Type help for the command catalog.\n\n",
            "system",
        )
        self.entry.focus_set()

    def activate(self) -> None:
        """Move keyboard focus into the persistent command line."""
        self.after_idle(self.entry.focus_set)

    def toggle(self) -> None:
        """Expand or collapse the dock while leaving its prompt available."""
        if self.docked:
            self._set_expanded(not self.expanded)
        self.activate()

    def _leave_console(self) -> None:
        if self.docked:
            self._set_expanded(False)
            self.activate()
        elif self._on_close is not None:
            self._on_close()
        elif self._window is not None:
            self._window.destroy()
        else:
            self.destroy()

    def _build(self) -> None:
        outer = tk.Frame(
            self, bg="#111614", padx=10 if self.docked else 12,
            pady=8 if self.docked else 12,
        )
        outer.pack(fill="both", expand=True)
        api_bar = tk.Frame(outer, bg="#17201c", padx=12, pady=9)
        api_bar.pack(fill="x", pady=(0, 9))
        tk.Label(
            api_bar, text="SDK CONSOLE", bg="#17201c", fg="#8cff65",
            font=("Segoe UI Semibold", 9),
        ).pack(side="left")
        tk.Label(
            api_bar,
            text="  AI API: allin1-sdk agent-api",
            bg="#17201c", fg="#c8d8d0", font=("Cascadia Mono", 9),
        ).pack(side="left")
        if self.docked:
            self.toggle_button = ttk.Button(
                api_bar, text="Expand", command=self.toggle, width=10,
            )
            self.toggle_button.pack(side="right")
        else:
            tk.Label(
                api_bar, text="local · audited · writes off by default",
                bg="#17201c", fg="#76bca0", font=("Segoe UI", 9),
            ).pack(side="right")

        self.console_body = tk.Frame(outer, bg="#111614")
        self.console_body.pack(fill="both", expand=True)
        self.output = tk.Text(
            self.console_body, bg="#0b0f0d", fg="#d8e6df", insertbackground="#8cff65",
            selectbackground="#28623c", relief="flat", padx=12, pady=10,
            font=("Cascadia Mono", 10), wrap="word", state="disabled",
            height=8 if self.docked else 20,
        )
        scroll = ttk.Scrollbar(
            self.console_body, orient="vertical", command=self.output.yview,
        )
        self.output.configure(yscrollcommand=scroll.set)
        self.output.pack(side="top", fill="both", expand=True)
        scroll.place(relx=1.0, rely=0.0, relheight=0.72, anchor="ne")
        self.output.tag_configure("prompt", foreground="#8cff65")
        self.output.tag_configure("error", foreground="#ff857a")
        self.output.tag_configure("system", foreground="#76bca0")

        suggestion_frame = tk.Frame(self.console_body, bg="#111614")
        suggestion_frame.pack(fill="x", pady=(8, 6))
        self.suggestions = ttk.Treeview(
            suggestion_frame, columns=("kind", "description"), show="tree headings",
            height=4 if self.docked else 6, selectmode="browse",
        )
        self.suggestions.heading("#0", text="Completion")
        self.suggestions.heading("kind", text="Type")
        self.suggestions.heading("description", text="Description")
        self.suggestions.column("#0", width=360, stretch=True)
        self.suggestions.column("kind", width=80, stretch=False)
        self.suggestions.column("description", width=470, stretch=True)
        self.suggestions.pack(fill="x")
        self.suggestions.bind("<Double-1>", self._accept_suggestion)

        self.command_row = tk.Frame(outer, bg="#111614")
        self.command_row.pack(fill="x")
        tk.Label(
            self.command_row, text=">", bg="#111614", fg="#8cff65",
            font=("Cascadia Mono", 13, "bold"),
        ).pack(side="left", padx=(0, 8))
        self.command = tk.StringVar()
        self.entry = tk.Entry(
            self.command_row, textvariable=self.command, bg="#17201c", fg="#f0f7f3",
            insertbackground="#8cff65", relief="flat", bd=0,
            font=("Cascadia Mono", 11),
        )
        self.entry.pack(side="left", fill="x", expand=True, ipady=8, ipadx=8)
        self.hint = tk.StringVar(value="Start typing to filter commands")
        tk.Label(
            outer, textvariable=self.hint, anchor="w", bg="#111614", fg="#7f9b8e",
            font=("Segoe UI", 9),
        ).pack(fill="x", pady=(5, 0))
        self.command.trace_add("write", self._refresh_suggestions)
        self.entry.bind("<Return>", self._execute)
        self.entry.bind("<Tab>", self._accept_suggestion)
        self.entry.bind("<Up>", lambda event: self._move(-1, event))
        self.entry.bind("<Down>", lambda event: self._move(1, event))
        self.entry.bind("<Control-Up>", lambda event: self._history_move(-1, event))
        self.entry.bind("<Control-Down>", lambda event: self._history_move(1, event))
        self.entry.bind("<Control-l>", self._clear)
        self.entry.bind("<Escape>", self._escape)
        self.bind("<Control-Key-space>", self._accept_suggestion)
        self._refresh_suggestions()
        if self.docked:
            self._set_expanded(False)

    def _set_expanded(self, value: bool) -> None:
        if not self.docked:
            return
        self.expanded = value
        if value:
            self.console_body.pack(
                fill="both", expand=True, before=self.command_row, pady=(0, 7),
            )
        else:
            self.console_body.pack_forget()
        self.toggle_button.configure(text="Collapse" if value else "Expand")

    def _load_history(self) -> list[str]:
        try:
            return [line for line in self.history_path.read_text(encoding="utf-8").splitlines() if line][-200:]
        except OSError:
            return []

    def _save_history(self) -> None:
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            self.history_path.write_text("\n".join(self.history[-200:]) + "\n", encoding="utf-8")
        except OSError:
            pass

    def _append(self, value: str, tag: str = "") -> None:
        self.output.configure(state="normal")
        self.output.insert("end", value, tag)
        self.output.configure(state="disabled")
        self.output.see("end")

    def _refresh_suggestions(self, *_args: object) -> None:
        self.matches = suggestions_for(
            self.command.get(), cwd=self.project_root, history=self.history,
        )
        self.suggestions.delete(*self.suggestions.get_children())
        for number, match in enumerate(self.matches):
            self.suggestions.insert(
                "", "end", iid=str(number), text=match.label,
                values=(match.kind, match.description),
            )
        if self.matches:
            self.suggestions.selection_set("0")
            self.hint.set(f"Tab → {self.matches[0].replacement}")
        else:
            self.hint.set("Enter runs · Ctrl+L clears · Escape clears the command")

    def _selected_match(self) -> ConsoleSuggestion | None:
        selected = self.suggestions.selection()
        if not selected:
            return self.matches[0] if self.matches else None
        index = int(selected[0])
        return self.matches[index] if index < len(self.matches) else None

    def _accept_suggestion(self, _event: object | None = None) -> str:
        match = self._selected_match()
        if match:
            self.command.set(match.replacement)
            self.entry.icursor("end")
        return "break"

    def _move(self, delta: int, _event: object | None = None) -> str:
        if not self.command.get().strip() and delta < 0 and self.history:
            return self._history_move(delta)
        if self.matches:
            selected = self.suggestions.selection()
            current = int(selected[0]) if selected else 0
            target = min(max(current + delta, 0), len(self.matches) - 1)
            self.suggestions.selection_set(str(target))
            self.suggestions.see(str(target))
            self.hint.set(f"Tab → {self.matches[target].replacement}")
            return "break"
        return self._history_move(delta)

    def _history_move(self, delta: int, _event: object | None = None) -> str:
        if self.history:
            self.history_index = min(max(self.history_index + delta, 0), len(self.history))
            value = self.history[self.history_index] if self.history_index < len(self.history) else ""
            self.command.set(value)
            self.entry.icursor("end")
        return "break"

    def _execute(self, _event: object | None = None) -> str:
        value = self.command.get().strip()
        if not value or self.running:
            return "break"
        if not self.history or self.history[-1] != value:
            self.history.append(value)
            self.history = self.history[-200:]
            self._save_history()
        self.history_index = len(self.history)
        self.command.set("")
        self._append(f"> {value}\n", "prompt")
        self.running = True
        self.entry.configure(state="disabled")
        self.hint.set("Running command…")

        def worker() -> None:
            result = execute_console_command(value, self.history)
            try:
                self.after(0, lambda: self._finish(result))
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=worker, daemon=True).start()
        return "break"

    def _finish(self, result: ConsoleResult) -> None:
        if not self.winfo_exists():
            return
        if result.action == "clear":
            self._clear_output()
        elif result.action == "exit":
            self._leave_console()
            return
        elif result.output:
            self._append(result.output, "error" if result.exit_code else "")
            if not result.output.endswith("\n"):
                self._append("\n")
        self._append(f"[exit {result.exit_code}]\n\n", "system")
        self.running = False
        self.entry.configure(state="normal")
        self.entry.focus_set()
        self._refresh_suggestions()

    def _clear_output(self) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.configure(state="disabled")

    def _clear(self, _event: object | None = None) -> str:
        self._clear_output()
        return "break"

    def _escape(self, _event: object | None = None) -> str:
        if self.command.get():
            self.command.set("")
        else:
            self._leave_console()
        return "break"
