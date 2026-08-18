"""Console entry point for the packaged ALLIN1 SDK Agent API."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from allin1_sdk.agent_api import serve_stdio


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="ALLIN1-SDK-Agent",
        description="Serve the ALLIN1 SDK structured JSONL API over stdio.",
    )
    parser.add_argument(
        "--allow-game-writes", action="store_true",
        help=(
            "Permit guarded game/archive commands; their normal acknowledgement "
            "and safety checks still apply."
        ),
    )
    options = parser.parse_args(argv)
    serve_stdio(
        sys.stdin, sys.stdout, allow_game_writes=options.allow_game_writes,
    )


if __name__ == "__main__":
    main()
