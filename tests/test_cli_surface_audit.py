from __future__ import annotations

import importlib
import pkgutil

from click.testing import CliRunner

import allin1_sdk
from allin1_sdk.cli import main


def test_cli_reports_the_public_sdk_version() -> None:
    result = CliRunner().invoke(main, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"ALLIN1 SDK, version {allin1_sdk.__version__}"


def test_every_shipped_sdk_module_imports_without_optional_tool_startup() -> None:
    failures: list[str] = []
    for module in pkgutil.iter_modules(allin1_sdk.__path__):
        name = f"allin1_sdk.{module.name}"
        try:
            importlib.import_module(name)
        except BaseException as exc:  # Import audit must report even SystemExit.
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    assert failures == []


def test_every_top_level_cli_command_renders_help_without_side_effects():
    runner = CliRunner()
    failures: list[str] = []

    for name in sorted(main.commands):
        result = runner.invoke(main, [name, "--help"])
        if result.exit_code != 0:
            failures.append(
                f"{name}: exit={result.exit_code} exception={result.exception!r}"
            )

    assert failures == []


def test_every_legacy_sdk_alias_is_the_reviewed_top_level_command_and_has_help():
    runner = CliRunner()
    sdk = main.commands["sdk"]
    failures: list[str] = []

    for name, command in sorted(sdk.commands.items()):
        assert main.commands.get(name) is command
        result = runner.invoke(main, ["sdk", name, "--help"])
        if result.exit_code != 0:
            failures.append(
                f"{name}: exit={result.exit_code} exception={result.exception!r}"
            )

    assert failures == []
