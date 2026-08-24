"""Stable UTF-8 console entry point for source and frozen SDK builds."""

from __future__ import annotations

import os
import sys


def configure_utf8_stdio() -> None:
    """Make Windows console and redirected streams consistently emit UTF-8."""
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleCP(65001)
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except (AttributeError, OSError):
            pass
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass


def main() -> None:
    configure_utf8_stdio()
    from allin1_sdk.cli import main as cli

    cli(prog_name="allin1-sdk")


if __name__ == "__main__":
    main()
