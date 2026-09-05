"""Console entry point for the Tauri-owned ALLIN1 desktop sidecar."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from collections.abc import Sequence

from allin1_sdk.desktop_protocol import run_job_worker, serve_stdio


def main(argv: Sequence[str] | None = None) -> None:
    from allin1_sdk.console_entry import configure_utf8_stdio

    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="ALLIN1-SDK-Desktop-Sidecar",
        description="Serve the versioned ALLIN1 desktop JSONL protocol.",
    )
    parser.add_argument(
        "--allow-game-writes", action="store_true",
        help=(
            "Process-owner opt-in for guarded game/archive commands. The Tauri "
            "WebView cannot set this option and CLI acknowledgements still apply."
        ),
    )
    parser.add_argument(
        "--allow-package-writes", action="store_true",
        help=(
            "Process-owner opt-in limited to digest-bound managed package "
            "install and uninstall operations."
        ),
    )
    parser.add_argument("--allow-rpf-writes", action="store_true",
                        help="Native-owner opt-in limited to reviewed GTA mods RPF transactions.")
    parser.add_argument("--job-worker", action="store_true", help=argparse.SUPPRESS)
    options = parser.parse_args(argv)
    if getattr(sys, "frozen", False):
        from allin1_sdk import release_identity
        from allin1_sdk.release_identity import embedded_build_identity, verify_runtime_resources
        if embedded_build_identity() is None:
            raise RuntimeError("Frozen SDK sidecar is missing build provenance")
        verify_runtime_resources(
            Path(os.environ.get("ALLIN1_SDK_HOME", Path(sys.executable).parent.parent)),
            Path(release_identity.__file__).with_name("resource-checksums.json"),
        )
    if options.job_worker:
        raise SystemExit(run_job_worker(sys.stdin, sys.stdout))
    serve_stdio(
        sys.stdin, sys.stdout, allow_game_writes=options.allow_game_writes,
        allow_package_writes=options.allow_package_writes,
        allow_rpf_writes=options.allow_rpf_writes,
    )


if __name__ == "__main__":
    main()
